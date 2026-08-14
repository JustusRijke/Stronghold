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
- `settings.py` -- deployment settings from optional `settings.toml`, plus
  logging setup.
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

## SQL export

After every change `db.export()` rewrites `inventory.sql` next to the
database: for each table (in foreign-key dependency order) a
`CREATE TABLE IF NOT EXISTS` followed by one `INSERT` per row. The file is
readable and diff-friendly, so the data can live in git -- this is the backup
and recovery story. It is self-contained (schema included), so restoring is
just `sqlite3 inventory.db < inventory.sql` into an empty database, no prior
app run needed.

**The `.sql` is the source of truth, not the `.db`.** `db.init` drops the
database and replays the `.sql` into it on every startup, so the `.db` is a
disposable working copy (and stays gitignored). Rolling back is therefore
`git checkout <commit>` on the data folder plus a restart -- no export/import
step, no chance of the two files disagreeing. A `.db` with no `.sql` beside it
is refused rather than silently blessed as the truth, since that combination
means the real data went missing.

Two consequences worth knowing:

- The export is written atomically (temp file + `replace`), because it is now
  the only copy of the data.
- `INSERT`s omit NULL columns and the stock price caches
  (`_DERIVED_COLUMNS`; `refresh_all_prices` rebuilds them at startup), which
  cuts the file by about a quarter and keeps the rows readable. Only NULLable
  columns may be omitted -- our NOT NULL defaults are Python-side, so SQLite
  has nothing to fall back on -- and a module-level check enforces that.
  `Part.estimated_price` and `BuildLine.unit_price` are deliberately kept:
  both are hand-set for virtual parts and cannot be recomputed.

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
  `Available` or `Consumed by build order` (stock never disappears -- see build
  orders below); it replaces the old `active` flag for stock.
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
(a build's lifecycle; stock's `Available` vs `Consumed by build order`).
Referencers use plain FKs, no cascade. PO lines (order detail) and BOM lines
(build detail) are not master data, so they are plainly removable -- a PO line
only if not booked.

## Settings

Two kinds, split by one test: does a wrong value stop the app running or
being reachable?

- **Deployment** (`settings.py`): db path, GUI port, logging --
  read once at startup from optional `settings.toml` (stdlib `tomllib`), each
  key falling back to a default. See `settings.toml.example`. The whole
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
- **No schema migrations (alpha).** `db.init` calls `Base.metadata.create_all`,
  which only creates *missing tables* -- it never alters an existing one. So
  changing a model's columns (e.g. adding `Part.assembly`) does NOT update an
  existing `inventory.db`; the next query fails with `no such column`. During
  this rapid-prototyping phase the fix is to throw the old database away: delete
  `inventory.db` and let the app recreate it, or restore data from an
  `inventory.sql` that was exported *after* the schema change. A real migration
  path (Alembic, or a hand-written `ALTER TABLE` step) is 1.0 work, deferred
  until the data on disk is worth preserving.
