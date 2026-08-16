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

After every change `db.export()` rewrites `inventory.sql` next to the
database: for each table (in foreign-key dependency order) a
`CREATE TABLE IF NOT EXISTS` followed by one `INSERT` per row. The file is
readable and diff-friendly, so the data can live in git -- this is the backup
and recovery story. It is self-contained (schema included), so restoring is
just `sqlite3 inventory.db < inventory.sql` into an empty database, no prior
app run needed.

**The `.sql` is the source of truth, and the only file the user names.**
`settings.toml` configures `db.data_file` (the `.sql`); SQLite is an internal
detail. `db.init` builds a working `.db` from scratch under
`tempfile.gettempdir()/stronghold/`, named `<stem>-<hash of the .sql's
absolute path>.db` so two datasets never collide, and replays the `.sql` into
it on every startup. Nothing but the `.sql` ever appears in the data folder.
Rolling back is therefore `git checkout <commit>` on that folder plus a
restart -- no export/import step, and no second file that can disagree.

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
- The file opens with two `--` comment lines: the restore hint, and
  `-- Written by Stronghold <version>, data schema version <n>` (see below).

## Data versioning

The `.sql` records what wrote it, in two places written together by
`db.export`: a header comment for whoever opens the file, and two rows in the
`settings` table (`schema.version`, `app.version`) that the app reads back. The
rows are the authority -- a comment cannot survive a `sqlite3 < file` restore.
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
the *current* code's), and only then replays the `.sql`. The schema therefore
never needs altering -- which is also why Alembic buys nothing here. What can
be wrong is the replayed **data**:

| Change since the file was written | Replaying it into the current schema |
|---|---|
| Column added | Succeeds, and the new column silently takes its default |
| Column removed | Handled: the column is scaffolded back for the replay, then dropped (below) |
| Column renamed | Fails loudly: `table parts has no column named ...` |

The added-column case is the dangerous one precisely because it is silent, and
it is reached by a documented, routine operation: rolling back with
`git checkout` on an older `.sql` (see docs/deployment.md).

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
  primary key; `sku` is a plain human code. `assembly` marks a part built from
  other parts; `virtual` marks one with unlimited stock (e.g. labour) that never
  has a stock item and is only used for BOM pricing -- builds never consume it.
  `assembly` and `virtual` are mutually exclusive. `purchasable` (default true)
  marks a part that may be bought: only such a part can have supplier parts, and
  so appear on a purchase order.
- `BomLine(id, parent_part_id, component_part_id, quantity)` -- one component
  line of an assembly's bill of materials: the parent assembly needs `quantity`
  (float) of the component part. Unique per (parent, component). Build detail
  like `POLine` (no `active` flag), plainly removable.
- `StockItem(id, count, part_id, po_id, build_id, status)` -- an inventory item;
  the part supplies SKU and description. `count` is float (parts may be
  measured, not just counted); `po_id`/`build_id` are nullable FKs to the PO it
  was received against / the build that produced or consumed it. `status` is
  `available` or `consumed` (`models.StockStatus`; stock never disappears -- see
  build orders below), and it replaces the old `active` flag for stock. The
  stored value is a short code, not the label the UI shows: keeping the two
  apart is what stops a rewording from being a data migration.
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
- `Supplier(id, name)`, `SupplierPart(id, supplier_id, sku, part_id, ...)`
  (int PK; sku is a plain code, not unique), `PurchaseOrder(id, supplier_id,
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

## No hard deletes (soft-delete convention)

Records are never physically deleted, so no reference can dangle and
`inventory.sql` keeps the full picture. Parts, suppliers and supplier parts
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
  can live next to the data. See `settings.toml.example`. The whole
  InvenTree connection (url, username, password) lives here: `inventory.sql`
  is tracked in git, so credentials must stay out of the database.
- **Domain** (`db.DOMAIN_DEFAULTS`): data, e.g. the GUI title. Stored in the
  `settings` table (one row per changed key, unchanged keys read as their
  default), edited on the `/settings` page.

## Logging

`setup_logging()` sends timestamped records to a rolling file. Level, file
name, per-file rollover size (`rollover_kb`) and total-size budget
(`max_total_kb`) come from `[logging]` in `settings.toml`; a
`RotatingFileHandler` keeps `max_total / rollover` files. `main()` logs any
uncaught fatal error before exiting.

## Deliberate simplifications

Known ceilings, with their upgrade paths, deferred until they actually hurt
(marked `ponytail:` in the code):

- No undo. Recovery = git + `inventory.sql`. Bring the command log back when
  a real mistake needs stepping back.
- max+1 id generation; fine while one process owns the database.
- Single process, no locking or multi-user support.
- Money is float; switch to int cents if rounding bites.
- Domain settings are all strings; add typed casting back when a non-string
  setting appears.
- In-memory substring filter on the parts page; add an index when parts grow
  large.
- `_MIGRATIONS` is empty at 1.0: the mechanism ships, the first step arrives
  with the first change that makes an older file wrong. See "Data versioning".
