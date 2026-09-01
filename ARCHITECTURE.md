# Architecture

Stronghold is a small Python domain core (SQLite + SQLAlchemy) exposed over a
thin FastAPI JSON API, with a SvelteKit single-page app on top. The core has no
web-framework imports -- that separation is what let the UI move from NiceGUI to
SvelteKit by rewriting only the edges.

Backend (`backend/`):
- `models.py` -- every SQLAlchemy model (Part, BomLine, StockItem, Supplier,
  SupplierPart, PurchaseOrder, POLine, Booking, Setting).
- `db.py` -- engine/session setup, the `.sql` export, and every write
  operation as a plain function. The domain core; imports no web framework.
- `api.py` -- FastAPI routes wrapping every `db` function as JSON. Pure edge:
  request/response shaping and error mapping (`InventoryError` -> HTTP 400),
  no domain logic. Its OpenAPI schema is the frontend's contract.
- `inventree.py` -- read-only HTTP client for importing parts from an
  InvenTree server (stdlib urllib).
- `settings.py` -- deployment settings from the `settings.toml` given on the
  command line, plus logging setup.
- `main.py` -- entry point: init db, mount the API, and in production serve the
  built SPA from `frontend/build` (unknown non-`/api` paths fall back to
  `index.html` so client-side routing and deep links work).

Frontend (`frontend/`): SvelteKit 2 + Svelte 5 + TypeScript, built with
adapter-static to a plain SPA that FastAPI serves. `src/lib/api.ts` is a typed
client over the API; `src/lib/components/DataTable.svelte` is the reusable grid
(hyperlink rows, tri-state boolean filters, localStorage filter persistence,
live row count) done natively rather than as JS embedded in the backend.

## History

Until 2026-07 the project was a generic command-log kernel (event sourcing,
then snapshot-based undo/redo) with the domain built as auto-discovered
plugins on top. That framework was built before the product; it was collapsed
into a small monolith to iterate on real features first. In 2026-07 the UI was
split off: the NiceGUI monolith became a FastAPI core + SvelteKit SPA once the
data-grid needs outgrew what NiceGUI could express without embedding JavaScript
in Python strings. The command-log/undo design lives in git history and can
return the day a real feature demands it (e.g. undoing a fat-fingered booking)
-- feature-driven, not speculative.

## Writes

Every mutation is a function in `db.py` decorated with `_write`: it runs in
one transaction, is logged at INFO before commit (rejections at WARNING,
unexpected failures at ERROR with traceback), and on success rewrites the SQL
export. Validation failures raise `InventoryError` and roll back; `api.py` maps
them to HTTP 400 with the message, and the frontend shows it as a toast.

Durability follows from that: the data file is written (and, with
`auto_commit`, committed) as part of every write, so nothing is held in memory
waiting to be flushed -- even a `SIGKILL` loses nothing. The lifespan hook in
`main.py` exports once more on shutdown, so a stop always ends on a known-good
write; in practice it finds nothing to do.

The matching risk is at the *other* end. Startup replays the file and then
exports over it, so anything uncommitted in it -- a hand edit, or a write from
a session that died before committing -- would be destroyed. `db.init` calls
`_commit_pending_changes()` before the replay: with `auto_commit` on, a dirty
data file is committed first, leaving the previous state recoverable.

Startup's own export is not a domain write either, so it does not take its
commit message from the activity log -- that row describes some earlier
session's change. `_startup_message` names what startup actually did (recorded
the version, migrated the data, or simply opened it); a start that changes
nothing stages nothing and commits nothing.

## SQL export

After every change `db.export()` rewrites the data directory: **one `.sql`
file per table**, each holding a `CREATE TABLE IF NOT EXISTS` followed by one
`INSERT` per row. The files are readable and diff-friendly, so the data can
live in git -- this is the backup and recovery story. Each is self-contained
(schema included), so restoring is just `cat *.sql | sqlite3 inventory.db`
into an empty database, no prior app run needed.

One file per table, rather than one big one, for two reasons: a table is small
enough to open and read, and **only the files a write actually changes are
rewritten** (`_write_table` compares against what is on disk first). Editing a
supplier moves `suppliers.sql` and `activity.sql`; the other thirteen files
are left alone, so `git log -p inventory/stock_items.sql` is a usable history
of one table.

Replay order does not matter, and neither do inter-table constraints: the
replay runs over a raw `sqlite3` connection, where `PRAGMA foreign_keys` is
off by default (the ON pragma is attached to the SQLAlchemy engine only). The
files are still written and replayed in dependency order, for determinism
rather than necessity.

**The `.sql` files are the source of truth, and the directory holding them is
the only path the user names.** `settings.toml` configures `db.data_path` (the
directory); SQLite is an internal detail. `db.init` builds a working `.db`
from scratch under `tempfile.gettempdir()/stronghold/`, named
`<stem>-<hash of the directory's absolute path>.db` so two datasets never
collide, and replays the files into it on every startup. Nothing but the
`.sql` files ever appears in the data folder. Rolling back is therefore
`git checkout <commit>` on that folder plus a restart -- no export/import
step, and no second file that can disagree.

Data written before the split is a single `inventory.sql`. Pointing
`db.data_path` at that file still works: it is replayed once and re-exported
as a directory of the same name beside it. The original is left untouched --
the app never deletes data it did not write -- and can be removed by hand.

Three consequences worth knowing:

- Rebuilding costs ~0.4s for a 1.8MB dataset, against the ~1.9s
  `refresh_all_prices` already spends at startup. Cheap enough that caching
  the working copy would buy nothing.
- The export is written atomically (temp file + `replace`), because it is now
  the only copy of the data.
- `INSERT`s omit NULL columns and the stock price caches
  (`_DERIVED_COLUMNS`; `refresh_all_prices` rebuilds them at startup), which
  cuts the file by about a quarter and keeps the rows readable. Only NULLable
  columns may be omitted -- our NOT NULL defaults are Python-side, so SQLite
  has nothing to fall back on -- and a module-level check enforces that.
  `Part.estimated_price` and `BuildLine.unit_price` are deliberately kept:
  both are hand-set for virtual parts and cannot be recomputed.
- Every file opens with two `--` comment lines: the restore hint, and
  `-- Written by Stronghold <version>, data schema version <n>` (see below).

## Data versioning

The export records what wrote it, in two places written together by
`db.export`: a header comment in every file, for whoever opens one, and two
rows in the `settings` table (`schema.version`, `app.version`) -- in
`settings.sql` -- that the app reads back. The rows are the authority -- a
comment cannot survive a restore.
Both keys are app metadata, deliberately *not* in `db.DOMAIN_DEFAULTS`, so
`get_setting`/`set_setting` reject them and they never appear on the settings
page. `db._stamp_versions` writes them straight to the table rather than via
`set_setting`, which is `@_write`-decorated and would re-export from inside
`init`.

Two numbers, because they move at different rates:

- **`app.version`** -- the git tag (see Releases in the README), informational,
  answering "which Stronghold do I install to open this?". Only the bare
  `X.Y.Z` is stamped: `hatch-vcs` appends a `.devN+g<hash>.d<date>` suffix to
  untagged builds, which would otherwise rewrite (and, with `auto_commit`,
  re-commit) the data file on every developer build.
- **`schema.version`** -- a hand-maintained integer in `backend/version.py`,
  bumped only when the shape of the data changes. It is what decides whether a
  file needs migrating, so it must not move just because a release was cut.

### Why migration is about the file, not the database

`db.init` deletes the working `.db`, runs `create_all` (so the schema is always
the *current* code's), and only then replays the `.sql` files. The schema therefore
never needs altering -- which is also why Alembic buys nothing here. What can
be wrong is the replayed **data**:

| Change since the file was written | Replaying it into the current schema |
|---|---|
| Column added | Succeeds, and the new column silently takes its default |
| Column removed | Handled: the column is scaffolded back for the replay, then dropped (below) |
| Column renamed | Fails loudly: `table parts has no column named ...` |

The added-column case is the dangerous one precisely because it is silent, and
it is reached by a documented, routine operation: rolling back with
`git checkout` on an older export (see docs/deployment.md).

A **removed** column is the case that a plain replay cannot survive: the old
file's `INSERT`s name a column the current tables no longer have, so
`executescript` raises and startup dies *before* `_migrate` could fix anything.
`db._DROPPED_COLUMNS` closes that gap. It lists every column a migration
removed, keyed by the schema version that removed it, and `_import_sql`
`ALTER TABLE ... ADD COLUMN`s each one back before the replay so the old
`INSERT`s fit. The matching `_MIGRATIONS` step then drops it again for good.

The scaffold goes up unconditionally, because the version stamp lives *in* the
data and cannot be read until the replay has happened. That costs nothing for a
current file: the column is simply never populated, and the same step drops it
again. Renames are still unhandled -- do them as a drop plus an add, or add the
step when one is actually needed.

### The two directions

`db._migrate` runs after the replay:

- **Older file** -- each `db._MIGRATIONS[n]` runs in ascending order, then
  `init`'s own `export()` writes the upgraded file back, re-stamped. Steps
  transform replayed data; the tables are already current, the one exception
  being a dropped column, which needs both halves (the `_import_sql` scaffold
  above and a step here to take it down). A file with no stamp at all predates
  stamping and is treated as version 1.
- **Newer file** -- refused with `DataVersionError`, and `main` exits 1. This
  is a data-loss guard, not tidiness: this app exports only the columns it
  knows, so the first write would drop the newer ones and `auto_commit` would
  commit that loss over the only copy of the data.

## Domain model

- `Part(id, sku, description, active, assembly, virtual, purchasable)` -- the
  master catalog record every stock item references. `id` is a local max+1
  primary key; `sku` is an optional human code, unique when set -- a blank is
  stored as NULL, and SQLite counts every NULL as distinct, so any number of
  parts may have none. `assembly` marks a part built from
  other parts; `virtual` marks one with unlimited stock (e.g. labour) that never
  has a stock item and is only used for BOM pricing -- builds never consume it.
  `assembly` and `virtual` are mutually exclusive. `purchasable` (default true)
  marks a part that may be bought: only such a part can have supplier parts, and
  so appear on a purchase order.
- `BomLine(id, parent_part_id, component_part_id, quantity, note)` -- one
  component line of an assembly's bill of materials: the parent assembly needs
  `quantity` (float) of the component part. `note` is optional free text
  ("fit last"), never interpreted -- it only rides along to the BOM table.
  Unique per (parent, component). Build detail like `POLine` (no `active`
  flag), plainly removable.
- `StockItem(id, count, part_id, po_id, build_id, status)` -- an inventory item;
  the part supplies SKU and description. `count` is float (parts may be
  measured, not just counted); `po_id`/`build_id` are nullable FKs to the PO it
  was received against / the build that produced or consumed it. `status` is
  `available` or `consumed` (`models.StockStatus`; stock never disappears -- see
  build orders below), and it replaces the old `active` flag for stock. Like
  every enum column it is *stored* as a small int (`models.EnumCode`), so no
  display text reaches the data file and rewording is never a migration.
- `BuildOrder(id, part_id, quantity, status, ...)` -- an order to build an
  assembly `Part`. Produced incrementally: `produce_build(id, qty)` consumes the
  build's `BuildLine` snapshot FIFO for `qty` units and adds `qty` of the assembly as
  Available stock stamped `build_id`. Consuming splits a `Consumed by build
  order` item (stamped `build_id`, carrying the source `po_id`) off the source
  Available item rather than destroying count, so provenance and purchase price
  stay traceable. `build_id` is always the producing build and
  `consumed_by_build_id` the consuming one -- one row can be both, so they are
  separate columns; `imported_produced` carries InvenTree's own produced count
  for migrated builds, whose output stock InvenTree may have deleted. Reaching `quantity` flips status to `Complete`. Virtual
  components are never consumed. No `active` flag; `status` is the state.
- `BuildLine(id, build_id, component_part_id, quantity, unit_price)` -- the assembly's BOM
  as it stood when the build was created, per produced unit. A build is costed
  from what it actually consumed, so its intended components must not change
  under it when the part's BOM is later edited. `db.build_bom_drifted` compares
  the two and the build page offers a resync; `db.resync_build_lines` refuses
  once the build is `Complete` or `Cancelled`. `unit_price` is set only for
  virtual components: they are never consumed, so nothing else records what the
  build was costed at for labour, and a later change to the rate must not
  re-cost a finished build. The InvenTree import stamps it too, from the part's
  price of the day -- InvenTree keeps no historical rate per build, so that
  baseline is the best figure available. A null (a row written before the
  column existed) still falls back to the part's current estimate.
- `SalesOrder(id, wc_order_id, wc_number, customer_name, shipping_country,
  shipping_cost, status, date_created, booked)` -- a WooCommerce order, imported
  read-only. WooCommerce owns the commercial facts; Stronghold owns only what it
  cannot know, the parts behind a sold product. Unlike every other master record
  the `id` is **local** (max+1), not the source system's: this import runs
  repeatedly into a non-empty database, so WooCommerce ids would collide.
  `wc_order_id` (unique) carries the link and is what re-import matches on. The
  code (`SO-0042`) is derived from the pk like `po_ref`/`build_ref`.
  `SalesOrderLine(id, so_id, wc_line_id, sku, description, unit_price,
  quantity)` is one WooCommerce line item, replaced wholesale by a re-import
  (its `sku` is plain text, often empty, and never matched against `Part.sku`).
  `SalesOrderLinePart(id, line_id, part_id, quantity)` is the mapping -- one of
  the two sales tables the user writes to -- quantity being per sold unit, like
  `BomLine`. `ProductSku(sku, part_id)` is the other: the sold SKU mapped to the
  `Part` it is made of, many SKUs to one part (variants share a build), which is
  why the sku is the pk. **Any** part maps -- an assembly contributes its whole
  BOM, anything else one link of itself at quantity 1, since a line selling a
  single bolt has no BOM to copy and requiring one would leave exactly that case
  to be hand-linked forever. It only ever *prefills*:
  `db._prefill_so_parts` writes ordinary `SalesOrderLinePart` rows
  on a line that has none yet -- run per order by `prefill_so_parts` (POST
  `/sales-orders/{id}/prefill`, which logs) and for every order the WooCommerce
  import touches (which logs once for the run). Never on a line the user has
  already filled in, so editing a mapping or its BOM cannot rewrite an existing
  order. `db.book_sales_order` consumes those parts through the same
  `_consume_fifo` helper `produce_build` uses, stamping `consumed_by_so_id` and
  producing nothing: a sale ships stock out rather than turning it into
  something. Shortfalls become the same negative-count debt a short build
  leaves, settled by a later receipt (`_settle_stock_debt` handles both, keyed
  by `("build"|"sales-order", id)`, and skips the assembly-reprice step for a
  sale, which has no output). Unbooked, non-cancelled orders count in
  `db.part_demand`.
- `Supplier(id, name)`, `SupplierPart(id, supplier_id, sku, part_id, ...)`
  (int PK; `sku` is the supplier's own code -- optional, and unique *per
  supplier* rather than globally: two suppliers may use the same code for the
  same thing, one supplier using it twice is a mistake. A blank is NULL, as in
  `Part.sku`; a duplicate that is not blank -- a repeated "N/A" placeholder,
  say -- is cleared by migration 5 and reported in the log, since what counts
  as a placeholder is the user's convention, not the app's),
  `PurchaseOrder(id, supplier_id,
  ...)`, `POLine(id, po_id, supplier_part_id, quantity, received, price)`,
  `Booking` -- the buy side. Every PK is an integer, matching InvenTree's pks
  so the migration can copy them verbatim. Receiving a PO line creates a stock
  item counting `received_qty * supplier_part.pack_qty` (a line orders packs),
  stamps its `po_id`, bumps the line's `received`, and adds a `Booking` row --
  one transaction. Over-receiving is allowed. Money is float.

## InvenTree migration (one-shot)

Importing from InvenTree is a migration, not a product feature, so it lives in
a standalone script (`backend/migrate_inventree.py`), not the API or UI:

    uv run python migrate_inventree.py <url> <username> <password>

It fetches parts, BOM, suppliers, supplier-parts, purchase orders, PO lines,
build orders (with each build's component snapshot and the stock it consumed),
and available stock (`inventree.py`, read-only HTTP), **drops** the target
database, recreates the schema, then inserts everything in one transaction.
Because it's one-shot into an empty database, each InvenTree pk is copied
verbatim as our own pk -- no remap, no `inventree_id` column, and ids stay
traceable back to InvenTree for follow-up steps and debugging. Cancelled
purchase orders (and their lines) are dropped; only available stock is imported
(fetched directly, not replayed as bookings). Rows whose FK targets are missing,
and fractional stock quantities (rounded to int `count`), are skipped/adjusted
and listed in a printed report. Cancelled build orders are dropped; a fractional
build quantity aborts the import (assemblies are whole).

`backend/import_bom_notes.py` is a second one-shot, added when BOM line notes
arrived after the migration had already run:

    uv run python import_bom_notes.py <url> <username> <password> <settings.toml>

It is non-destructive -- it opens an existing dataset the way the app does and
only fills in notes on BOM lines it can match by (parent part, component part),
which works because the migration copied the InvenTree pks verbatim. A line
that already has a note here is left alone, so it never overwrites hand-typed
text. Like the migration, it is exempt from the data-versioning rules: do not
maintain it against later schema changes.

## No hard deletes (soft-delete convention)

Records are never physically deleted, so no reference can dangle and the
export keeps the full picture. Parts, suppliers and supplier parts
carry an `active` flag and are deactivated; UIs hide inactive records by
default and pickers offer only active ones. Purchase orders, build orders and
stock items have no `active` flag -- their `status` field carries the state
(a build's lifecycle; stock's `available` vs `consumed`).
Referencers use plain FKs, no cascade. PO lines (order detail) and BOM lines
(build detail) are not master data, so they are plainly removable -- a PO line
only if not booked.

## Settings

Two kinds, split by one test: does a wrong value stop the app running or
being reachable?

- **Deployment** (`settings.py`): data file, GUI port, logging --
  read once at startup from the `settings.toml` named on the command line
  (stdlib `tomllib`), each key falling back to a default. Relative paths in it
  resolve against its own directory (`Settings.path_of`), so the settings file
  can live next to the data. See `settings.toml.example`. The InvenTree
  connection (url, username, password) lives here, but that is a one-shot
  migration script rather than the app -- see below for how the app's own
  credentials are handled.
- **Domain** (`db.DOMAIN_DEFAULTS`): data, e.g. the GUI title. Stored in the
  `settings` table (one row per changed key, unchanged keys read as their
  default), edited on the `/settings` page.

### Credentials

The export is tracked in git, so a credential in the `settings` table
would be committed (in `settings.sql`). They are therefore **encrypted at rest**:
`db.SECRET_SETTINGS` names the domain settings that are credentials, and
`get_setting`/`set_setting` transparently decrypt and encrypt those through
`backend/crypto.py` (`cryptography.fernet`, so a hand-edited value fails to
decrypt rather than yielding garbage).

Three consequences worth stating, because each is a deliberate trade:

- **The flag is code, not data.** A `secret` column on the row could be edited
  to `false` in the data file, and the next export would write the plaintext.
- **The value never leaves the backend.** `SettingOut` carries `value=""` plus
  `configured: bool`, so an unchanged form field cannot round-trip a secret
  back out, and `set_setting` logs that a secret changed without logging either
  value (the activity log is exported too).
- **The key is a separate, gitignored file** (`[secrets] key_file`), created at
  first run with `0600`. Every failure path in `crypto.py` returns `None`
  rather than raising, so a missing or unusable key degrades to "not
  configured" and the user re-enters the credential -- it never stops the app
  starting. That is the whole recovery story: losing the key costs the stored
  credentials and nothing else.

This is encryption, not hashing, and necessarily so: the WooCommerce client
sends the real key and secret on every request, so the value has to come back.

## Logging

`setup_logging()` sends timestamped records to a rolling file. Level, file
name, per-file rollover size (`rollover_kb`) and total-size budget
(`max_total_kb`) come from `[logging]` in `settings.toml`; a
`RotatingFileHandler` keeps `max_total / rollover` files. `main()` logs any
uncaught fatal error before exiting.

## Deliberate simplifications

Known ceilings, with their upgrade paths, deferred until they actually hurt
(marked `ponytail:` in the code):

- No undo. Recovery = git + the exported `.sql` files. Bring the command log
  back when a real mistake needs stepping back.
- max+1 id generation; fine while one process owns the database.
- Single process, no locking or multi-user support.
- Money is float; switch to int cents if rounding bites.
- Domain settings are all strings; add typed casting back when a non-string
  setting appears.
- In-memory substring filter on the parts page; add an index when parts grow
  large.
- `_MIGRATIONS` now runs to schema 8: 2 dropped the stored order references, 3
  moved the enum columns from text to int codes, 4 added the sales-order tables
  (additive, so its step is a no-op -- but it must exist, since `_migrate` walks
  every version in turn), 5 made `parts.sku` optional and unique, 6 added the
  optional `bom_lines.note` (additive, so a no-op step like 4), 7 added
  `stock_items.created_at` (additive but NOT NULL, so its step backfills from
  the order that created each row), 8 added `product_skus` (additive, a no-op
  step). See "Data versioning".
- Step 5 needed the mirror image of the `_DROPPED_COLUMNS` scaffold: an older
  file may hold rows that *violate* a constraint the current schema has (many
  parts sharing an empty sku), so the replay would fail before `_migrate` could
  clean them. `_import_sql` therefore drops the unique indexes in
  `db._SKU_INDEXES` before replaying and `db._rebuild_sku_indexes` recreates
  them afterwards -- on every startup, since the drop is unconditional. That is
  also why each constraint is a named `Index` rather than `unique=True` on the
  column: SQLite renders the latter inline, where `ALTER TABLE` cannot reach
  it.
