"""Database setup, the .sql export, and every write operation.

Each write is a plain function running in one transaction; on success the
whole database is re-exported to <db>.sql (one INSERT per row) so the data
can live in git. The .sql is the source of truth: the .db is dropped and
rebuilt from it at startup, so rolling back is `git checkout <commit>` plus a
restart.
"""

import functools
import hashlib
import json
import logging
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path

from models import (
    STOCK_AVAILABLE,
    STOCK_CONSUMED,
    Activity,
    Base,
    BomLine,
    Booking,
    BuildLine,
    BuildOrder,
    BuildStatus,
    Part,
    POLine,
    POStatus,
    PriceBasis,
    PurchaseOrder,
    Setting,
    StockItem,
    Supplier,
    SupplierPart,
)
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

_log = logging.getLogger(__name__)

_engine = None
_export_path = None
_export_enabled = True
_db_path = None

# domain settings: data, editable in the GUI, stored in the settings table
# ponytail: all values are strings; add typed casting back when a non-str setting appears
DOMAIN_DEFAULTS = {
    "gui.title": "Stronghold",
    # stocktake reason suggestions, comma separated. Split by direction: finding
    # stock and losing it have little vocabulary in common.
    "stocktake.add_reasons": "Found,Refurbished/repaired,Returned by customer,Unknown",
    "stocktake.subtract_reasons": "Damaged,Warranty claim by customer,Lost,Unknown",
}


class InventoryError(Exception):
    """A write cannot apply to the current state."""


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def suspend_export() -> None:
    """Stop exporting after every write, for a bulk load that would otherwise
    rewrite the whole file per row. The loader MUST finish with
    `export(force=True)` -- until it does, nothing is being persisted."""
    global _export_enabled
    _export_enabled = False


def init(sql_path: Path) -> None:
    """Open the data in `sql_path`. That file is the truth (tracked in git,
    restorable by checking out an older commit); SQLite is only how we query
    it, so the .db is rebuilt from scratch in a temp directory on every
    startup and never has to be looked after."""
    global _engine, _export_path, _db_path, _export_enabled
    _export_path = sql_path
    _export_enabled = True  # a previous suspend_export must not leak in here
    _db_path = _working_db_path(sql_path)
    _db_path.unlink(missing_ok=True)
    _engine = create_engine(f"sqlite:///{_db_path}")
    event.listen(_engine, "connect", _enable_foreign_keys)
    Base.metadata.create_all(_engine)
    if _export_path.exists():
        _import_sql()
    _export_path.parent.mkdir(parents=True, exist_ok=True)
    export()


def _working_db_path(sql_path: Path) -> Path:
    """A per-data-file scratch database under the system temp directory. Named
    after the .sql's full path so two datasets never share one working copy."""
    digest = hashlib.sha256(str(sql_path.resolve()).encode()).hexdigest()[:12]
    directory = Path(tempfile.gettempdir()) / "stronghold"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{sql_path.stem}-{digest}.db"


def _import_sql() -> None:
    """Replay the .sql into the freshly created (empty) database."""
    script = _export_path.read_text(encoding="utf-8")
    with sqlite3.connect(_db_path) as conn:
        conn.executescript(script)
    _log.info("loaded %s (working copy: %s)", _export_path, _db_path)


def session() -> Session:
    """Read access; writes go through the functions below."""
    return Session(_engine)


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (str, date)):
        return "'" + str(value).replace("'", "''") + "'"
    raise TypeError(f"cannot export a {type(value).__name__} value")


# Price caches: recomputed by refresh_all_prices() at startup, so exporting
# them is noise. Only NULLable columns can be listed -- an omitted column falls
# back to its *schema* default on restore, and our NOT NULL columns default in
# Python, not in SQL. That rules out parts.price_partial (bool NOT NULL).
# Part.estimated_price is deliberately kept: a virtual part's price is hand-set
# and cannot be recomputed. So is BuildLine.unit_price, the only record of the
# rate a build was costed at.
_DERIVED_COLUMNS = {
    "stock_items": {"unit_price", "price_po_id"},  # price_basis is NOT NULL
}

for _table, _skipped in _DERIVED_COLUMNS.items():
    _not_null = {c.name for c in Base.metadata.tables[_table].columns if not c.nullable}
    if _not_null & _skipped:
        raise RuntimeError(
            f"_DERIVED_COLUMNS lists NOT NULL column(s) {_not_null & _skipped} "
            f"on {_table}: restoring the .sql would fail"
        )


def export(force: bool = False) -> None:
    """Write the whole database as one INSERT statement per row; readable,
    diffable, git-friendly. This file is the truth -- the .db is rebuilt from
    it at startup -- so it is written atomically. NULL and derived columns are
    left out of each INSERT; the schema defaults cover them on restore.

    `force` writes even while a bulk load has export suspended -- that is how
    the loader persists its result at the end."""
    if not _export_enabled and not force:
        return
    lines = [
        "-- Stronghold data. Restore into a fresh (empty) database: sqlite3 inventory.db < inventory.sql"
    ]
    with Session(_engine) as s:
        conn = s.connection()
        for table in Base.metadata.sorted_tables:
            ddl = str(CreateTable(table, if_not_exists=True).compile(_engine)).strip()
            lines.append(f"{ddl};")
            skip = _DERIVED_COLUMNS.get(table.name, frozenset())
            names = [c.name for c in table.columns]
            # S608: table/column names come from our own model metadata, never
            # from user input -- there is no injection vector here.
            for row in conn.exec_driver_sql(
                f'SELECT * FROM "{table.name}" ORDER BY rowid'  # noqa: S608
            ):
                pairs = [
                    (name, value)
                    for name, value in zip(names, row)
                    if value is not None and name not in skip
                ]
                columns = ", ".join(name for name, _ in pairs)
                values = ", ".join(_literal(value) for _, value in pairs)
                lines.append(f"INSERT INTO {table.name} ({columns}) VALUES ({values});")  # noqa: S608
    # atomic: a crash mid-write must not truncate the only copy of the data
    tmp = _export_path.with_name(_export_path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(_export_path)


def _write(fn):
    """Run fn(session, ...) in one transaction, log it, re-export on success."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _log.debug(
            "%s %s %s", fn.__name__, args, kwargs
        )  # before commit: failures matter
        try:
            with Session(_engine) as s, s.begin():
                fn(s, *args, **kwargs)
        except InventoryError as error:
            _log.warning("%s rejected: %s", fn.__name__, error)
            raise
        except Exception:
            _log.exception("%s failed unexpectedly and rolled back", fn.__name__)
            raise
        export()

    return wrapper


def _activity(s: Session, action: str, message: str, refs: list[tuple]) -> None:
    """Append an activity-log row inside the caller's transaction (so it commits
    atomically with the action). refs: list of (type, id, label) tuples."""
    s.add(
        Activity(
            action=action,
            message=message,
            refs=json.dumps([{"type": t, "id": i, "label": l} for t, i, l in refs]),
        )
    )


# -- settings ---------------------------------------------------------------


def get_setting(key: str) -> str:
    if key not in DOMAIN_DEFAULTS:
        raise InventoryError(f"unknown setting '{key}'")
    with session() as s:
        row = s.get(Setting, key)
    return DOMAIN_DEFAULTS[key] if row is None else row.value


@_write
def set_setting(s: Session, key: str, value: str) -> None:
    if key not in DOMAIN_DEFAULTS:
        raise InventoryError(f"unknown setting '{key}'")
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value


# -- parts ------------------------------------------------------------------


def get_part(s: Session, part_id: int) -> Part:
    part = s.get(Part, part_id)
    if part is None:
        raise InventoryError(f"no part with id {part_id}")
    return part


def next_part_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(Part.id))) or 0) + 1


def _validate_sku(sku: str) -> None:
    if len(sku) > 64 or any(c.isspace() for c in sku):
        raise InventoryError(f"invalid sku {sku!r}: max 64 chars, no whitespace")


@_write
def create_part(
    s: Session, part_id: int, sku: str, description: str, virtual: bool = False
) -> None:
    _validate_sku(sku)
    if s.get(Part, part_id) is not None:
        raise InventoryError(f"part id {part_id} already exists")
    s.add(Part(id=part_id, sku=sku, description=description, virtual=virtual))
    _activity(s, "create_part", f"Created part {description}", [("part", part_id, sku)])


@_write
def edit_part(s: Session, part_id: int, description: str) -> None:
    get_part(s, part_id).description = description


@_write
def set_part_sku(s: Session, part_id: int, sku: str) -> None:
    get_part(s, part_id).sku = sku


@_write
def set_part_active(s: Session, part_id: int, active: bool) -> None:
    part = get_part(s, part_id)
    part.active = active
    verb = "Activated" if active else "Deactivated"
    _activity(
        s,
        "set_part_active",
        f"{verb} part {part.description}",
        [("part", part_id, part.sku)],
    )


# InvenTree import is a one-shot migration, not a product feature: see
# migrate_inventree.py. It builds a fresh db via the normal write functions
# below, so no InvenTree concept (pks, upsert-by-source) lives in the domain.


# -- pricing ----------------------------------------------------------------
# Part.estimated_price is a cache, recomputed inside the transaction of every
# write that can change it (PO line price/add/remove, booking, BOM edits). The
# rule: a bought part is worth its latest purchase price; an assembly is worth
# the sum of its components (recursively). Nothing is ever priced at 0.0 by
# accident -- POLine.price is not nullable, so 0 means "not filled in".


def latest_po_price_source(s: Session, part_id: int) -> tuple[float, int, str] | None:
    """(unit price, po id, po reference) from the most recent non-cancelled PO
    that priced this part, or None. Ordered by order date (ids do not follow
    date order); a line's price is per pack, so divide by pack_qty."""
    row = s.execute(
        select(
            POLine.price,
            SupplierPart.pack_qty,
            PurchaseOrder.id,
            PurchaseOrder.reference,
        )
        .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
        .join(PurchaseOrder, POLine.po_id == PurchaseOrder.id)
        .where(
            SupplierPart.part_id == part_id,
            POLine.price > 0,
            SupplierPart.pack_qty > 0,
            PurchaseOrder.status != POStatus.CANCELLED,
        )
        .order_by(
            PurchaseOrder.start_date.desc(), PurchaseOrder.id.desc(), POLine.id.desc()
        )
        .limit(1)
    ).first()
    return (row[0] / row[1], row[2], row[3]) if row else None


def latest_po_unit_price(s: Session, part_id: int) -> float | None:
    source = latest_po_price_source(s, part_id)
    return source[0] if source else None


def _compute_price(
    s: Session, part_id: int, seen: set[int]
) -> tuple[float | None, bool]:
    """(unit price, partial) for one part. An assembly sums its components'
    prices * quantity; `partial` means at least one component had no price, so
    the sum is a floor. A part revisited within one walk is a BOM cycle: it
    contributes nothing rather than recursing forever (cycles are not blocked
    on write, see add_bomline)."""
    part = get_part(s, part_id)
    if part.virtual:
        # a virtual part (labour, overhead) is never purchased: its price is
        # entered by hand and kept as-is
        return part.estimated_price, part.estimated_price is None
    if not part.assembly:
        price = latest_po_unit_price(s, part_id)
        return price, price is None
    if part_id in seen:  # a cycle: this assembly is its own ancestor
        return None, True
    lines = list(
        s.execute(
            select(BomLine.component_part_id, BomLine.quantity).where(
                BomLine.parent_part_id == part_id
            )
        )
    )
    if not lines:
        return None, True  # an assembly with no BOM has nothing to price
    seen.add(part_id)
    total = 0.0
    partial = False
    try:
        for component_id, quantity in lines:
            price, sub_partial = _compute_price(s, component_id, seen)
            partial = partial or sub_partial
            if price is not None:
                total += price * quantity
    finally:
        seen.discard(part_id)  # path-scoped: a diamond is not a cycle
    return (total if total > 0 else None), partial


def _parents_of(s: Session, part_id: int) -> list[int]:
    return list(
        s.scalars(
            select(BomLine.parent_part_id).where(BomLine.component_part_id == part_id)
        )
    )


def refresh_part_price(s: Session, part_id: int) -> None:
    """Recompute this part's cached price and that of every assembly that
    contains it, transitively. Called inside the caller's transaction.
    ponytail: walks up from one part; a full rebuild is refresh_all_prices."""
    todo = [part_id]
    done: set[int] = set()
    while todo:
        current = todo.pop()
        if current in done:
            continue
        done.add(current)
        part = get_part(s, current)
        price, partial = _compute_price(s, current, set())
        part.estimated_price = price
        part.price_partial = partial
        todo.extend(_parents_of(s, current))
    s.flush()  # stock prices read the estimates just written
    for touched in done:
        refresh_stock_prices_for_part(s, touched)


def _po_unit_price_for(
    s: Session, po_id: int, item_id: int, part_id: int
) -> float | None:
    """What the order this stock came from paid per item, or None if that order
    has no price for it. Prefers the booked line (the exact line received into
    this item); migrated stock has no Booking, so fall back to that order's
    line for the same part."""
    booked = s.execute(
        select(POLine.price, SupplierPart.pack_qty)
        .join(Booking, Booking.po_line_id == POLine.id)
        .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
        .where(Booking.stock_item_id == item_id, SupplierPart.pack_qty > 0)
        .limit(1)
    ).first()
    if booked is None:
        booked = s.execute(
            select(POLine.price, SupplierPart.pack_qty)
            .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
            .where(
                POLine.po_id == po_id,
                SupplierPart.part_id == part_id,
                SupplierPart.pack_qty > 0,
            )
            .order_by(POLine.id)
            .limit(1)
        ).first()
    if booked is None:
        return None
    # price 0 means "not filled in": the order exists but does not price this
    return booked[0] / booked[1] if booked[0] > 0 else None


def build_unit_cost(s: Session, build_id: int) -> tuple[float | None, bool]:
    """(cost per produced unit, exact) for a build order, from the stock it
    actually consumed. `exact` is False when some consumed row had no price of
    its own or was itself a guess -- which covers a shortage, since the missing
    quantity is consumed at the part's estimate until the parts are received.
    Returns (None, False) when nothing was consumed or nothing produced.

    Consumed rows are priced first (they are inputs), so this reads their
    already-resolved unit_price -- it does not re-derive component prices."""
    consumed = list(
        s.scalars(
            select(StockItem).where(
                StockItem.consumed_by_build_id == build_id,
                StockItem.status == STOCK_CONSUMED,
            )
        )
    )
    if not consumed:
        return None, False
    # divide by what the build MADE, never by what it ordered: a build half way
    # through has spent its inputs on the units it has actually produced, and
    # dividing by the full order would price them at a fraction of their cost.
    # produced_qty covers output already sold on or consumed by a later build,
    # and imported history, so it is the true made-count.
    produced = produced_qty(s, build_id)
    if produced <= 0:
        return None, False

    total = 0.0
    exact = True
    for item in consumed:
        if item.unit_price is None:
            exact = False
        else:
            total += item.unit_price * item.count
            # an input that was itself a guess makes the output a guess
            if item.price_basis in (
                PriceBasis.ESTIMATE,
                PriceBasis.BUILD_PARTIAL,
                PriceBasis.PO_NO_PRICE,
            ):
                exact = False
    if total <= 0:
        return None, False

    # a shortage means the consumed rows total less than the BOM asks, so the
    # cost is a floor rather than the true one
    taken: dict[int, float] = {}
    for item in consumed:
        taken[item.part_id] = taken.get(item.part_id, 0.0) + item.count
    for _lid, component_id, _sku, _desc, qty, virtual, *_price in build_lines_for(
        s, build_id
    ):
        if virtual:
            continue
        if taken.get(component_id, 0.0) + 1e-9 < qty * produced:
            exact = False
            break
    return total / produced, exact


def refresh_stock_price(s: Session, item: StockItem) -> None:
    """Price one stock item: what its own order paid, else the part's estimate.
    This is deliberately not the part price -- two items of the same part bought
    on different orders are worth different amounts."""
    # a virtual consumption row was priced at the rate its build snapshotted;
    # repricing it to the part's current rate would undo that freeze
    if item.price_basis == PriceBasis.VIRTUAL:
        return
    if item.po_id is not None:
        unit = _po_unit_price_for(s, item.po_id, item.id, item.part_id)
        if unit is not None:
            item.unit_price = unit
            item.price_basis = PriceBasis.PO
            item.price_po_id = item.po_id
            return
    # produced by a build: worth what that build actually consumed. build_id is
    # the producing build even on a row later eaten by another build, so this
    # holds for assembly output whatever became of it.
    if item.build_id is not None:
        unit, exact = build_unit_cost(s, item.build_id)
        if unit is not None:
            item.unit_price = unit
            item.price_basis = PriceBasis.BUILD if exact else PriceBasis.BUILD_PARTIAL
            item.price_po_id = None
            return
    part = get_part(s, item.part_id)
    if part.estimated_price is not None:
        source = latest_po_price_source(s, item.part_id)
        item.unit_price = part.estimated_price
        item.price_basis = PriceBasis.ESTIMATE
        item.price_po_id = source[1] if source else None
        return
    # nothing prices this part anywhere
    item.unit_price = None
    item.price_basis = PriceBasis.PO_NO_PRICE if item.po_id else PriceBasis.NONE
    item.price_po_id = item.po_id


def refresh_stock_prices_for_part(s: Session, part_id: int) -> None:
    """Reprice every stock item of one part (its estimate may have moved)."""
    for item in s.scalars(select(StockItem).where(StockItem.part_id == part_id)):
        refresh_stock_price(s, item)


@_write
def refresh_all_prices(s: Session) -> None:
    """Recompute every part's price, then every stock item's. The repair/
    backfill path (e.g. after an import); normal writes keep prices current
    incrementally."""
    for part_id in list(s.scalars(select(Part.id))):
        part = get_part(s, part_id)
        price, partial = _compute_price(s, part_id, set())
        part.estimated_price = price
        part.price_partial = partial
    s.flush()  # stock prices read the part estimates just written
    # consumed rows first: build-produced stock is costed from their prices
    for consumed_first in (STOCK_CONSUMED, STOCK_AVAILABLE):
        for item in s.scalars(
            select(StockItem)
            .where(StockItem.status == consumed_first)
            .order_by(StockItem.id)
        ):
            refresh_stock_price(s, item)


# -- bom --------------------------------------------------------------------


def get_bomline(s: Session, line_id: int) -> BomLine:
    line = s.get(BomLine, line_id)
    if line is None:
        raise InventoryError(f"no bom line {line_id}")
    return line


def next_bomline_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(BomLine.id))) or 0) + 1


def bom_for(s: Session, parent_part_id: int) -> list:
    """(line_id, component_id, component_sku, component_description, quantity,
    component_virtual, component_price, component_price_partial) for one
    assembly's BOM, joined to Part for display."""
    return list(
        s.execute(
            select(
                BomLine.id,
                Part.id,
                Part.sku,
                Part.description,
                BomLine.quantity,
                Part.virtual,
                Part.estimated_price,
                Part.price_partial,
            )
            .join(Part, BomLine.component_part_id == Part.id)
            .where(BomLine.parent_part_id == parent_part_id)
            .order_by(BomLine.id)
        )
    )


def _snapshot_build_lines(s: Session, build_id: int, part_id: int) -> None:
    """Copy a part's current BOM onto a build as its BuildLine snapshot,
    replacing any existing one. Caller supplies the transaction."""
    for old in s.scalars(select(BuildLine).where(BuildLine.build_id == build_id)):
        s.delete(old)
    s.flush()
    next_id = (s.scalar(select(func.max(BuildLine.id))) or 0) + 1
    for _lid, component_id, _sku, _desc, qty, virtual, price, _partial in bom_for(
        s, part_id
    ):
        s.add(
            BuildLine(
                id=next_id,
                build_id=build_id,
                component_part_id=component_id,
                quantity=qty,
                # only virtual components need it: their price is hand-set and
                # nothing else records what this build was costed at
                unit_price=price if virtual else None,
            )
        )
        next_id += 1


def build_lines_for(s: Session, build_id: int) -> list:
    """(line_id, component_id, sku, description, quantity, virtual, price,
    price_partial) for one build's snapshot -- same shape as bom_for. `price` is
    the snapshot's own price where it has one (virtual components), so a later
    change to the part's rate cannot re-cost a finished build; legacy rows
    snapshotted without a price fall back to the part's current estimate."""
    return list(
        s.execute(
            select(
                BuildLine.id,
                Part.id,
                Part.sku,
                Part.description,
                BuildLine.quantity,
                Part.virtual,
                func.coalesce(BuildLine.unit_price, Part.estimated_price),
                Part.price_partial,
            )
            .join(Part, BuildLine.component_part_id == Part.id)
            .where(BuildLine.build_id == build_id)
            .order_by(BuildLine.id)
        )
    )


def build_bom_drifted(s: Session, build_id: int) -> bool:
    """True when the part's live BOM no longer matches this build's snapshot
    (component added/removed or a quantity changed)."""
    build = get_build(s, build_id)
    snap = {c: q for _l, c, _s, _d, q, *_r in build_lines_for(s, build_id)}
    live = {c: q for _l, c, _s, _d, q, *_r in bom_for(s, build.part_id)}
    if snap.keys() != live.keys():
        return True
    return any(abs(live[c] - q) > 1e-9 for c, q in snap.items())


@_write
def resync_build_lines(s: Session, build_id: int) -> None:
    """Re-snapshot an active build from its part's current BOM. Refused once the
    build is Complete or Cancelled: a finished build's intent is history."""
    build = get_build(s, build_id)
    if build.status in (BuildStatus.COMPLETE, BuildStatus.CANCELLED):
        raise InventoryError(
            f"build {build_id} is {build.status}; its components cannot be resynced"
        )
    _snapshot_build_lines(s, build_id, build.part_id)
    label = build.reference or f"BO-{build_id}"
    _activity(
        s,
        "resync_build_lines",
        f"Resynced {label} components to the current BOM",
        [("build", build_id, label)],
    )


@_write
def set_part_assembly(s: Session, part_id: int, assembly: bool) -> None:
    part = get_part(s, part_id)
    if assembly and part.virtual:
        raise InventoryError(f"part {part_id} is virtual; cannot be an assembly")
    if not assembly and s.scalar(
        select(BomLine).where(BomLine.parent_part_id == part_id).limit(1)
    ):
        raise InventoryError(
            f"part {part_id} still has bom lines; remove them before unmarking"
        )
    if assembly and s.scalar(
        select(SupplierPart).where(SupplierPart.part_id == part_id).limit(1)
    ):
        raise InventoryError(
            f"part {part_id} is sold by a supplier; assemblies are built, not purchased"
        )
    part.assembly = assembly
    s.flush()
    refresh_part_price(s, part_id)  # the pricing rule itself changed


@_write
def set_part_virtual(s: Session, part_id: int, virtual: bool) -> None:
    part = get_part(s, part_id)
    if virtual and part.assembly:
        raise InventoryError(f"part {part_id} is an assembly; cannot be virtual")
    part.virtual = virtual
    if not virtual:
        part.estimated_price = None  # a hand-entered price no longer applies
    s.flush()
    refresh_part_price(s, part_id)  # the pricing rule itself changed


@_write
def set_part_purchasable(s: Session, part_id: int, purchasable: bool) -> None:
    part = get_part(s, part_id)
    if not purchasable and s.scalar(
        select(SupplierPart).where(SupplierPart.part_id == part_id).limit(1)
    ):
        raise InventoryError(
            f"part {part_id} is sold by a supplier; remove those supplier parts first"
        )
    part.purchasable = purchasable


@_write
def set_part_price(s: Session, part_id: int, price: float | None) -> None:
    """Set a virtual part's price by hand. Only virtual parts have one: every
    other part's price is derived (latest PO, or the BOM roll-up)."""
    part = get_part(s, part_id)
    if not part.virtual:
        raise InventoryError(
            f"part {part_id} is not virtual; its price is derived, not set"
        )
    if price is not None and price < 0:
        raise InventoryError("price cannot be negative")
    part.estimated_price = price
    part.price_partial = price is None
    s.flush()
    refresh_part_price(s, part_id)  # cascade to the assemblies using it


@_write
def add_bomline(
    s: Session,
    line_id: int,
    parent_part_id: int,
    component_part_id: int,
    quantity: float,
) -> None:
    if quantity <= 0:
        raise InventoryError("bom quantity must be positive")
    if parent_part_id == component_part_id:
        # ponytail: only trivial self-reference blocked; multi-hop cycles not checked
        raise InventoryError("a part cannot be a component of itself")
    if s.get(BomLine, line_id) is not None:
        raise InventoryError(f"bom line id {line_id} already exists")
    parent = get_part(s, parent_part_id)
    if not parent.assembly:
        raise InventoryError(f"part {parent_part_id} is not an assembly")
    get_part(s, component_part_id)  # component must exist
    if s.scalar(
        select(BomLine)
        .where(
            BomLine.parent_part_id == parent_part_id,
            BomLine.component_part_id == component_part_id,
        )
        .limit(1)
    ):
        raise InventoryError(
            f"part {component_part_id} is already a component of {parent_part_id}"
        )
    s.add(
        BomLine(
            id=line_id,
            parent_part_id=parent_part_id,
            component_part_id=component_part_id,
            quantity=quantity,
        )
    )
    s.flush()
    refresh_part_price(s, parent_part_id)


@_write
def edit_bomline_quantity(s: Session, line_id: int, quantity: float) -> None:
    if quantity <= 0:
        raise InventoryError("bom quantity must be positive")
    line = get_bomline(s, line_id)
    line.quantity = quantity
    s.flush()
    refresh_part_price(s, line.parent_part_id)


@_write
def remove_bomline(s: Session, line_id: int) -> None:
    # ponytail: bom lines are order detail, plain delete (not master data)
    line = get_bomline(s, line_id)
    parent_part_id = line.parent_part_id
    s.delete(line)
    s.flush()
    refresh_part_price(s, parent_part_id)


# -- stock ------------------------------------------------------------------


def get_item(s: Session, item_id: int) -> StockItem:
    item = s.get(StockItem, item_id)
    if item is None:
        raise InventoryError(f"no stock item with id {item_id}")
    return item


def next_item_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(StockItem.id))) or 0) + 1


@_write
def create_item(s: Session, item_id: int, part_id: int) -> None:
    if s.get(StockItem, item_id) is not None:
        raise InventoryError(f"stock item id {item_id} already exists")
    get_part(s, part_id)  # part must exist
    s.add(StockItem(id=item_id, count=0.0, part_id=part_id))
    s.flush()
    refresh_stock_price(s, get_item(s, item_id))  # no PO: the part estimate


@_write
def set_count(s: Session, item_id: int, count: float) -> None:
    if count < 0:
        raise InventoryError(f"count of item {item_id} cannot be {count}")
    item = get_item(s, item_id)
    old = item.count
    item.count = count
    if count != old:
        part = get_part(s, item.part_id)
        _activity(
            s,
            "set_count",
            f"Adjusted stock of {part.description} from {old:g} to {count:g}",
            [
                ("stock", item_id, f"{count:g}x {part.description}"),
                ("part", item.part_id, part.sku),
            ],
        )


@_write
def set_item_status(s: Session, item_id: int, status: str) -> None:
    get_item(s, item_id).status = status


def on_hand(s: Session, part_id: int) -> float:
    """Available stock for one part; negative debt rows net out."""
    return (
        s.scalar(
            select(func.coalesce(func.sum(StockItem.count), 0.0)).where(
                StockItem.part_id == part_id, StockItem.status == STOCK_AVAILABLE
            )
        )
        or 0.0
    )


def owed(s: Session, part_id: int) -> float:
    """What a part already owes builds, as a negative number (0.0 if nothing).
    This is the floor a stocktake can count down to: emptying the shelf leaves
    the debt, and only add_negative_stock may deepen it."""
    return (
        s.scalar(
            select(func.coalesce(func.sum(StockItem.count), 0.0)).where(
                StockItem.part_id == part_id,
                StockItem.status == STOCK_AVAILABLE,
                StockItem.count < 0,
            )
        )
        or 0.0
    )


@_write
def stocktake(
    s: Session,
    part_id: int,
    count: float,
    reason: str,
    item_id: int | None = None,
    build_id: int | None = None,
    po_id: int | None = None,
) -> None:
    """Correct a part's on-hand stock to a counted figure. Always needs a reason.

    Counting more creates a stock item for the surplus. Counting less draws the
    difference down FIFO across items, or off one named item, splitting off a
    consumed row per source so what left -- and the price it was bought at --
    stays traceable; stock never just disappears. A stocktake only ever empties
    the shelf, so the floor is whatever the part already owes: it never creates
    a debt, which is add_negative_stock's job."""
    part = get_part(s, part_id)
    if not reason:
        raise InventoryError("a stocktake needs a reason")
    # a part already owing stock counts below zero; the debt rows are not the
    # shelf, so a stocktake may empty the shelf but never deepen the debt
    floor = owed(s, part_id)
    if count < floor - 1e-9:
        raise InventoryError(
            f"a stocktake cannot count below {floor:g} for this part; "
            "add a negative stock item instead"
        )
    if build_id is not None:
        get_build(s, build_id)
    if po_id is not None:
        get_po(s, po_id)
    current = on_hand(s, part_id)
    delta = count - current
    if abs(delta) <= 1e-9:
        return
    # one timestamp for every row this stocktake writes, so they group as one
    # event. Naive local time, like Activity.at.
    at = datetime.now()  # noqa: DTZ005
    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1

    if delta > 0:
        s.add(
            StockItem(
                id=next_id,
                count=delta,
                part_id=part_id,
                po_id=po_id,
                build_id=build_id,
                status=STOCK_AVAILABLE,
                stocktake_at=at,
                stocktake_reason=reason,
            )
        )
        s.flush()
        refresh_stock_price(s, get_item(s, next_id))
    else:
        # no item named: walk the shelf FIFO, spilling onto the next item as
        # each runs out. A named item is the only source, and must cover it.
        if item_id is None:
            sources = s.scalars(
                select(StockItem)
                .where(
                    StockItem.part_id == part_id,
                    StockItem.status == STOCK_AVAILABLE,
                    StockItem.count > 0,  # never draw down an outstanding debt row
                )
                .order_by(StockItem.id)
            ).all()
        else:
            named = get_item(s, item_id)
            if named.part_id != part_id or named.status != STOCK_AVAILABLE:
                raise InventoryError(
                    f"stock item {item_id} is not available stock of part {part_id}"
                )
            sources = [named]
        need = -delta
        available = sum(i.count for i in sources)
        if need > available + 1e-9:
            where = (
                f"stock item {sources[0].id} holds {available:g}"
                if item_id is not None
                else f"only {available:g} of part {part_id} is in stock"
            )
            raise InventoryError(f"{where}, cannot take {need:g}")
        for source in sources:
            if need <= 1e-9:
                break
            take = min(source.count, need)
            source.count -= take
            need -= take
            s.add(
                StockItem(
                    id=next_id,
                    count=take,
                    part_id=part_id,
                    po_id=source.po_id,  # keep the source's price provenance
                    build_id=source.build_id,
                    status=STOCK_CONSUMED,
                    unit_price=source.unit_price,
                    price_basis=source.price_basis,
                    price_po_id=source.price_po_id,
                    stocktake_at=at,
                    stocktake_reason=reason,
                )
            )
            next_id += 1

    refs = [("part", part_id, part.sku)]
    if build_id is not None:
        build = get_build(s, build_id)
        refs.append(("build", build_id, build.reference or f"BO-{build_id}"))
    _activity(
        s,
        "stocktake",
        f"Stocktake of {part.description}: {current:g} -> {count:g} ({reason})",
        refs,
    )


@_write
def add_negative_stock(
    s: Session, part_id: int, quantity: float, build_id: int, reason: str = ""
) -> None:
    """Record stock a build consumed that was never booked in.

    Mostly a migration repair: an imported build completed without its stock
    fully allocated. Writes the same pair produce_build's shortage does, so
    receiving the parts settles it and reprices the build."""
    part = get_part(s, part_id)
    build = get_build(s, build_id)
    if quantity <= 0:
        raise InventoryError("quantity must be positive")
    at = datetime.now()  # noqa: DTZ005
    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    price = part.estimated_price
    basis = PriceBasis.ESTIMATE if price is not None else PriceBasis.NONE
    # consumed row first, debt row second, adjacent ids -- _settle_stock_debt
    # pairs them by `debt.id - 1`, so a later receipt settles this too
    for count in (quantity, -quantity):
        s.add(
            StockItem(
                id=next_id,
                count=count,
                part_id=part_id,
                consumed_by_build_id=build_id,
                status=STOCK_CONSUMED if count > 0 else STOCK_AVAILABLE,
                unit_price=price,
                price_basis=basis,
                stocktake_at=at,
                stocktake_reason=reason,
            )
        )
        next_id += 1
    label = build.reference or f"BO-{build_id}"
    _activity(
        s,
        "add_negative_stock",
        f"Recorded {quantity:g}x {part.description} used by {label} but never booked",
        [("part", part_id, part.sku), ("build", build_id, label)],
    )


@_write
def settle_debt_from_stock(
    s: Session, debt_id: int, quantity: float, item_id: int | None = None
) -> None:
    """Pay off a build shortfall out of stock already on the shelf.

    The receipt path (_settle_stock_debt) does the same when parts arrive on a
    PO; this is the manual version for a part that ended up with both a debt row
    and available stock. The debt shrinks, the sources are drawn down FIFO (or
    off one named item) and the build's placeholder consumption is repriced to
    what that stock actually cost, so the assembly stops being costed at a
    guess."""
    debt = get_item(s, debt_id)
    if debt.status != STOCK_AVAILABLE or debt.count >= 0:
        raise InventoryError(f"stock item {debt_id} is not an outstanding debt")
    if quantity <= 0:
        raise InventoryError("quantity must be positive")
    if quantity > -debt.count + 1e-9:
        raise InventoryError(f"stock item {debt_id} only owes {-debt.count:g}")
    build_id = debt.consumed_by_build_id
    if build_id is None:
        raise InventoryError(f"stock item {debt_id} is not linked to a build order")
    # the debt's immediate predecessor, same pairing contract produce_build and
    # add_negative_stock write and _settle_stock_debt relies on
    placeholder = s.get(StockItem, debt_id - 1)
    if (
        placeholder is None
        or placeholder.status != STOCK_CONSUMED
        or placeholder.consumed_by_build_id != build_id
        or placeholder.part_id != debt.part_id
    ):
        raise InventoryError(f"stock item {debt_id} has no consumption to settle")
    if item_id is None:
        sources = s.scalars(
            select(StockItem)
            .where(
                StockItem.part_id == debt.part_id,
                StockItem.status == STOCK_AVAILABLE,
                StockItem.count > 0,  # never settle a debt out of another debt
            )
            .order_by(StockItem.id)
        ).all()
    else:
        named = get_item(s, item_id)
        if (
            named.part_id != debt.part_id
            or named.status != STOCK_AVAILABLE
            or named.count <= 0
        ):
            raise InventoryError(
                f"stock item {item_id} is not available stock of part {debt.part_id}"
            )
        sources = [named]
    available = sum(i.count for i in sources)
    if quantity > available + 1e-9:
        raise InventoryError(f"only {available:g} in stock, cannot settle {quantity:g}")

    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    need = quantity
    for source in sources:
        if need <= 1e-9:
            break
        take = min(source.count, need)
        source.count -= take
        need -= take
        debt.count += take
        # one consumed row per source, so each keeps its own price provenance;
        # the placeholder shrinks by what the real stock now covers
        placeholder.count -= take
        s.add(
            StockItem(
                id=next_id,
                count=take,
                part_id=debt.part_id,
                po_id=source.po_id,
                build_id=source.build_id,
                consumed_by_build_id=build_id,
                status=STOCK_CONSUMED,
                unit_price=source.unit_price,
                price_basis=source.price_basis,
                price_po_id=source.price_po_id,
            )
        )
        next_id += 1
    s.flush()
    # the assembly was costed off the estimate; its inputs are real now
    for produced in s.scalars(
        select(StockItem).where(
            StockItem.build_id == build_id, StockItem.status == STOCK_AVAILABLE
        )
    ):
        refresh_stock_price(s, produced)
    part = get_part(s, debt.part_id)
    build = get_build(s, build_id)
    label = build.reference or f"BO-{build_id}"
    _activity(
        s,
        "settle_debt_from_stock",
        f"{quantity:g}x {part.description} settled a shortfall on {label} from stock",
        [
            ("stock", debt_id, f"{debt.count:g}x {part.description}"),
            ("build", build_id, label),
            ("part", part.id, part.sku),
        ],
    )


# -- purchasing -------------------------------------------------------------


def get_supplier(s: Session, supplier_id: int) -> Supplier:
    supplier = s.get(Supplier, supplier_id)
    if supplier is None:
        raise InventoryError(f"no supplier with id {supplier_id}")
    return supplier


def get_supplier_part(s: Session, sp_id: int) -> SupplierPart:
    part = s.get(SupplierPart, sp_id)
    if part is None:
        raise InventoryError(f"no supplier part {sp_id}")
    return part


def get_po(s: Session, po_id: int) -> PurchaseOrder:
    po = s.get(PurchaseOrder, po_id)
    if po is None:
        raise InventoryError(f"no purchase order {po_id}")
    return po


def get_po_line(s: Session, line_id: int) -> POLine:
    line = s.get(POLine, line_id)
    if line is None:
        raise InventoryError(f"no po line {line_id}")
    return line


def next_supplier_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(Supplier.id))) or 0) + 1


def next_supplier_part_id() -> int:
    with session() as s:
        return (s.scalar(select(func.max(SupplierPart.id))) or 0) + 1


def _check_reference_free(s: Session, model, reference: str, own_id: int) -> None:
    """A reference is the order's human code -- refuse to hand the same one to
    two orders. Case-insensitive: PO-0007 and po-0007 are the same code."""
    clash = s.scalar(
        select(model.id).where(
            func.lower(model.reference) == reference.lower(), model.id != own_id
        )
    )
    if clash is not None:
        raise InventoryError(f"reference {reference} is already used by order {clash}")


def next_po_id() -> int:
    with session() as s:
        return (s.scalar(select(func.max(PurchaseOrder.id))) or 0) + 1


def next_line_id() -> int:
    with session() as s:
        return (s.scalar(select(func.max(POLine.id))) or 0) + 1


def _validate_code(code: str, what: str) -> None:
    if not code or len(code) > 64 or any(c.isspace() for c in code):
        raise InventoryError(f"invalid {what} {code!r}: 1-64 chars, no whitespace")


@_write
def create_supplier(s: Session, supplier_id: int, name: str) -> None:
    if s.get(Supplier, supplier_id) is not None:
        raise InventoryError(f"supplier {supplier_id} already exists")
    s.add(Supplier(id=supplier_id, name=name))
    _activity(
        s,
        "create_supplier",
        f"Created supplier {name}",
        [("supplier", supplier_id, name)],
    )


@_write
def edit_supplier(s: Session, supplier_id: int, name: str) -> None:
    get_supplier(s, supplier_id).name = name


@_write
def set_supplier_active(s: Session, supplier_id: int, active: bool) -> None:
    get_supplier(s, supplier_id).active = active


def _check_purchasable(s: Session, part_id: int) -> None:
    """A supplier only sells a part that is bought at all: not an assembly (those
    are built) and not one marked non-purchasable."""
    part = get_part(s, part_id)
    if part.assembly:
        raise InventoryError(
            f"part {part_id} is an assembly; assemblies are built, not purchased"
        )
    if not part.purchasable:
        raise InventoryError(f"part {part_id} is not purchasable")


@_write
def create_supplier_part(
    s: Session,
    sp_id: int,
    supplier_id: int,
    sku: str,
    part_id: int,
    description: str = "",
    ean: str = "",
    hyperlink: str = "",
    pack_qty: int = 1,
) -> None:
    _validate_code(sku, "supplier part sku")
    if s.get(SupplierPart, sp_id) is not None:
        raise InventoryError(f"supplier part {sp_id} already exists")
    get_supplier(s, supplier_id)
    _check_purchasable(s, part_id)
    s.add(
        SupplierPart(
            id=sp_id,
            supplier_id=supplier_id,
            sku=sku,
            part_id=part_id,
            description=description,
            ean=ean,
            hyperlink=hyperlink,
            pack_qty=pack_qty,
        )
    )
    part = get_part(s, part_id)
    _activity(
        s,
        "create_supplier_part",
        f"Created supplier part {sku} for {part.description}",
        [("supplier-part", sp_id, sku), ("part", part_id, part.sku)],
    )


@_write
def edit_supplier_part(
    s: Session,
    sp_id: int,
    part_id: int,
    description: str = "",
    ean: str = "",
    hyperlink: str = "",
    pack_qty: int = 1,
) -> None:
    sp = get_supplier_part(s, sp_id)
    _check_purchasable(s, part_id)
    was_part_id = sp.part_id
    sp.part_id = part_id
    sp.description = description
    sp.ean = ean
    sp.hyperlink = hyperlink
    sp.pack_qty = pack_qty  # divides the line price, so prices move with it
    s.flush()
    refresh_part_price(s, part_id)
    if was_part_id != part_id:
        refresh_part_price(s, was_part_id)


@_write
def set_supplier_part_active(s: Session, sp_id: int, active: bool) -> None:
    get_supplier_part(s, sp_id).active = active


@_write
def create_po(
    s: Session,
    po_id: int,
    supplier_id: int,
    reference: str = "",
    status: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
    delivery_cost: float = 0.0,
    supplier_reference: str = "",
    description: str = "",
) -> None:
    get_supplier(s, supplier_id)
    if s.get(PurchaseOrder, po_id) is not None:
        raise InventoryError(f"purchase order {po_id} already exists")
    # every order carries a human code; default to the pk in the same
    # zero-padded shape the InvenTree import produced (PO-0042)
    reference = reference.strip() or f"PO-{po_id:04d}"
    _check_reference_free(s, PurchaseOrder, reference, po_id)
    s.add(
        PurchaseOrder(
            id=po_id,
            supplier_id=supplier_id,
            reference=reference,
            status=status,
            # the order date drives "latest price"; a dateless order cannot be
            # ranked, so new orders are stamped today when none is given
            start_date=start_date or date.today(),
            end_date=end_date,
            delivery_cost=delivery_cost,
            supplier_reference=supplier_reference,
            description=description,
        )
    )
    _activity(s, "create_po", f"Created {reference}", [("po", po_id, reference)])


@_write
def edit_po(
    s: Session,
    po_id: int,
    reference: str = "",
    status: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
    delivery_cost: float = 0.0,
    supplier_reference: str = "",
    description: str = "",
) -> None:
    po = get_po(s, po_id)
    # status transition rules: Complete/Cancelled are dead ends (mirrors builds);
    # Cancelled additionally requires nothing has been received yet, since
    # receipts already created real stock that cancelling can't undo.
    if po.status in (POStatus.COMPLETE, POStatus.CANCELLED) and status != po.status:
        raise InventoryError(
            f"purchase order {po_id} is {po.status}, cannot change status"
        )
    if status == POStatus.CANCELLED and _po_has_receipts(s, po_id):
        raise InventoryError(
            f"purchase order {po_id} has received lines, cannot cancel"
        )
    if start_date is None:
        raise InventoryError("purchase order start date is required")
    if not reference.strip():
        raise InventoryError("purchase order reference is required")
    reference = reference.strip()
    _check_reference_free(s, PurchaseOrder, reference, po_id)
    reprice = status != po.status or start_date != po.start_date
    po.reference = reference
    po.status = status
    po.start_date = start_date
    po.end_date = end_date
    po.delivery_cost = delivery_cost
    po.supplier_reference = supplier_reference
    po.description = description
    if reprice:
        # cancelling drops this order's prices; the date decides which order is
        # "latest" -- either way every part on it may be repriced
        s.flush()
        for part_id in set(
            s.scalars(
                select(SupplierPart.part_id)
                .join(POLine, POLine.supplier_part_id == SupplierPart.id)
                .where(POLine.po_id == po_id)
            )
        ):
            refresh_part_price(s, part_id)


def _po_has_receipts(s: Session, po_id: int) -> bool:
    return bool(
        s.scalar(
            select(POLine).where(POLine.po_id == po_id, POLine.received > 0).limit(1)
        )
    )


@_write
def add_po_line(
    s: Session,
    line_id: int,
    po_id: int,
    supplier_part_id: int,
    quantity: int,
    price: float,
) -> None:
    if s.get(POLine, line_id) is not None:
        raise InventoryError(f"po line id {line_id} already exists")
    po = get_po(s, po_id)
    if po.status in (POStatus.COMPLETE, POStatus.CANCELLED):
        raise InventoryError(f"purchase order {po_id} is {po.status}, cannot add lines")
    sp = get_supplier_part(s, supplier_part_id)
    if sp.supplier_id != po.supplier_id:
        raise InventoryError(
            f"supplier part {supplier_part_id} is not from the PO's supplier"
        )
    s.add(
        POLine(
            id=line_id,
            po_id=po_id,
            supplier_part_id=supplier_part_id,
            quantity=quantity,
            price=price,
        )
    )
    s.flush()
    refresh_part_price(s, sp.part_id)


@_write
def edit_po_line(
    s: Session,
    line_id: int,
    quantity: int | None = None,
    price: float | None = None,
) -> None:
    line = get_po_line(s, line_id)
    _check_po_line_editable(s, line)
    if quantity is not None:
        if quantity <= 0:
            raise InventoryError("po line quantity must be positive")
        if quantity < line.received:
            raise InventoryError(
                f"po line {line_id}: quantity {quantity} below {line.received} already received"
            )
        line.quantity = quantity
    if price is not None:
        if price < 0:
            raise InventoryError("po line price must not be negative")
        line.price = price
        s.flush()  # the new price must be visible to the recompute below
        # price feeds latest_po_unit_price, and any stock booked against this
        # line was valued at the old one
        refresh_part_price(s, get_supplier_part(s, line.supplier_part_id).part_id)
        for item in s.scalars(
            select(StockItem)
            .join(Booking, Booking.stock_item_id == StockItem.id)
            .where(Booking.po_line_id == line_id)
        ):
            refresh_stock_price(s, item)


@_write
def remove_po_line(s: Session, line_id: int) -> None:
    # ponytail: PO lines are order detail, plain delete (not master data)
    line = get_po_line(s, line_id)
    _check_po_line_editable(s, line)
    if s.scalar(select(Booking).where(Booking.po_line_id == line_id).limit(1)):
        raise InventoryError(f"po line {line_id} has bookings, cannot remove")
    part_id = get_supplier_part(s, line.supplier_part_id).part_id
    s.delete(line)
    s.flush()  # the line must be gone before "latest price" is recomputed
    refresh_part_price(s, part_id)


def _check_po_line_editable(s: Session, line: POLine) -> None:
    po = get_po(s, line.po_id)
    if po.status in (POStatus.COMPLETE, POStatus.CANCELLED):
        raise InventoryError(
            f"purchase order {po.id} is {po.status}, cannot edit lines"
        )


def _settle_stock_debt(s: Session, item: StockItem) -> dict[int, float]:
    """Pay off outstanding build shortfalls for a part out of freshly received
    stock. A shortfall left a negative Available row plus a placeholder consumed
    row priced at the part's estimate; the parts have now arrived, so the debt
    shrinks toward zero and the consumed row is stamped with this PO and
    repriced to what was actually paid -- the build stops being costed at a
    guess. Settled units never reach the shelf, so `item` is reduced by them.
    Returns {build_id: quantity settled}."""
    debts = s.scalars(
        select(StockItem)
        .where(
            StockItem.part_id == item.part_id,
            StockItem.status == STOCK_AVAILABLE,
            StockItem.count < 0,
            StockItem.consumed_by_build_id.is_not(None),
        )
        .order_by(StockItem.id)
    ).all()
    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    settled: dict[int, float] = {}
    for debt in debts:
        if item.count <= 1e-9:
            break
        # produce_build writes the pair together, consumed row first, so the
        # placeholder is the debt's immediate predecessor. Matching on
        # build+part instead would pick up the rows consumed from real stock.
        placeholder = s.get(StockItem, debt.id - 1)
        if (
            placeholder is None
            or placeholder.status != STOCK_CONSUMED
            or placeholder.consumed_by_build_id != debt.consumed_by_build_id
            or placeholder.part_id != debt.part_id
        ):
            continue  # hand-edited data: no consumption to reprice, leave it
        pay = min(-debt.count, item.count, placeholder.count)
        if pay <= 1e-9:
            continue
        if pay < placeholder.count - 1e-9:
            # partial receipt: only the settled units get this PO's price, the
            # rest stay owed at the estimate
            placeholder.count -= pay
            placeholder = StockItem(
                id=next_id,
                count=pay,
                part_id=debt.part_id,
                consumed_by_build_id=debt.consumed_by_build_id,
                status=STOCK_CONSUMED,
            )
            s.add(placeholder)
            next_id += 1
        placeholder.po_id = item.po_id
        s.flush()
        refresh_stock_price(s, placeholder)
        debt.count += pay
        item.count -= pay
        # not-null: the debt query filters consumed_by_build_id is_not(None)
        build_id: int = debt.consumed_by_build_id  # ty: ignore[invalid-assignment]
        settled[build_id] = settled.get(build_id, 0.0) + pay
    # the assemblies those builds produced were costed off the estimate; now
    # that their inputs are real, reprice the output
    for build_id in settled:
        for produced in s.scalars(
            select(StockItem).where(
                StockItem.build_id == build_id, StockItem.status == STOCK_AVAILABLE
            )
        ):
            refresh_stock_price(s, produced)
    return settled


def _receive_line(
    s: Session, line: POLine, stock_item_id: int, quantity: float
) -> tuple[StockItem, dict[int, float]]:
    """Receive `quantity` packs of one PO line into a new stock item: create it,
    book it to the line, price it, and pay off any build shortfalls for that part
    out of it. Returns (the stock item, {build_id: quantity settled}). Shared by
    book_po_line and book_po so the two receive paths cannot drift apart."""
    supplier_part = get_supplier_part(s, line.supplier_part_id)
    s.add(
        StockItem(
            id=stock_item_id,
            count=quantity * supplier_part.pack_qty,
            part_id=supplier_part.part_id,
            po_id=line.po_id,
        )
    )
    line.received += quantity
    s.flush()  # stock item must exist before the booking FK references it
    s.add(Booking(stock_item_id=stock_item_id, po_line_id=line.id))
    s.flush()  # the booking must exist before the new stock is priced from it
    item = get_item(s, stock_item_id)
    refresh_stock_price(s, item)
    return item, _settle_stock_debt(s, item)


def _settle_activity(
    s: Session, settled: dict[int, float], part: Part, po_id: int, po_label: str
) -> None:
    """One activity row per build a receipt paid off, so receiving tells the user
    what it settled instead of silently zeroing rows."""
    for build_id, qty in settled.items():
        build = get_build(s, build_id)
        build_label = build.reference or f"BO-{build_id}"
        _activity(
            s,
            "settle_stock_debt",
            f"{qty:g}x {part.description} settled a shortfall on {build_label}",
            [
                ("po", po_id, po_label),
                ("build", build_id, build_label),
                ("part", part.id, part.description),
            ],
        )


@_write
def book_po_line(s: Session, line_id: int, stock_item_id: int, quantity: float) -> None:
    """Receive `quantity` ordered units of a PO line into stock, in one
    transaction: create a stock item counting quantity * the supplier part's
    pack_qty (a line orders packs; each pack holds pack_qty items), link it to
    its PO and to the line (a Booking row), and add quantity to the line's
    received total. Over-receiving (received > ordered) is allowed."""
    if quantity <= 0:
        raise InventoryError("booking quantity must be positive")
    line = get_po_line(s, line_id)
    po = get_po(s, line.po_id)
    if po.status == POStatus.CANCELLED:
        raise InventoryError(f"purchase order {po.id} is cancelled, cannot receive")
    if s.get(StockItem, stock_item_id) is not None:
        raise InventoryError(f"stock item id {stock_item_id} already exists")
    supplier_part = get_supplier_part(s, line.supplier_part_id)
    count = quantity * supplier_part.pack_qty
    _, settled = _receive_line(s, line, stock_item_id, quantity)
    part = get_part(s, supplier_part.part_id)
    po = get_po(s, line.po_id)
    po_label = po.reference or f"PO-{po.id}"
    _activity(
        s,
        "book_po_line",
        f"Received {count:g}x {part.description} into stock",
        [
            ("po", po.id, po_label),
            ("stock", stock_item_id, f"{count:g}x {part.description}"),
        ],
    )
    _settle_activity(s, settled, part, po.id, po_label)
    _complete_po_if_fully_received(s, line.po_id)


def _complete_po_if_fully_received(s: Session, po_id: int) -> None:
    """Once every line of a PO is fully received, mark it Complete."""
    po = s.get(PurchaseOrder, po_id)
    if po is None or po.status == POStatus.COMPLETE:
        return
    lines = s.scalars(select(POLine).where(POLine.po_id == po_id)).all()
    if lines and all(line.received >= line.quantity for line in lines):
        po.status = POStatus.COMPLETE


@_write
def book_po(s: Session, po_id: int) -> None:
    """Receive every line's outstanding quantity (ordered - received) into stock
    in one transaction; fully-received lines are skipped. Marks the PO Complete."""
    po = get_po(s, po_id)
    if po.status == POStatus.CANCELLED:
        raise InventoryError(f"purchase order {po_id} is cancelled, cannot receive")
    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    po_label = po.reference or f"PO-{po_id}"
    refs = [("po", po_id, po_label)]
    all_settled: list[tuple[dict[int, float], Part]] = []
    for line in s.scalars(select(POLine).where(POLine.po_id == po_id)):
        outstanding = line.quantity - line.received
        if outstanding <= 0:
            continue
        supplier_part = get_supplier_part(s, line.supplier_part_id)
        count = outstanding * supplier_part.pack_qty
        _, settled = _receive_line(s, line, next_id, outstanding)
        part = get_part(s, supplier_part.part_id)
        refs.append(("stock", next_id, f"{count:g}x {part.description}"))
        all_settled.append((settled, part))
        next_id += 1
    if len(refs) > 1:
        _activity(s, "book_po", f"Received {po_label} into stock", refs)
    for settled, part in all_settled:
        _settle_activity(s, settled, part, po_id, po_label)
    _complete_po_if_fully_received(s, po_id)


# -- building ---------------------------------------------------------------


def get_build(s: Session, build_id: int) -> BuildOrder:
    build = s.get(BuildOrder, build_id)
    if build is None:
        raise InventoryError(f"no build order {build_id}")
    return build


def next_build_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(BuildOrder.id))) or 0) + 1


@_write
def create_build(
    s: Session,
    build_id: int,
    part_id: int,
    quantity: int,
    reference: str = "",
    status: str = "",
    description: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
) -> None:
    if quantity <= 0:
        raise InventoryError("build quantity must be positive")
    if s.get(BuildOrder, build_id) is not None:
        raise InventoryError(f"build order {build_id} already exists")
    part = get_part(s, part_id)
    if not part.assembly:
        raise InventoryError(f"part {part_id} is not an assembly")
    # every order carries a human code; default to the pk in the same
    # zero-padded shape the InvenTree import produced (BO-0042), as create_po does
    reference = reference.strip() or f"BO-{build_id:04d}"
    _check_reference_free(s, BuildOrder, reference, build_id)
    s.add(
        BuildOrder(
            id=build_id,
            part_id=part_id,
            quantity=quantity,
            reference=reference,
            status=status,
            description=description,
            start_date=start_date,
            end_date=end_date,
        )
    )
    _snapshot_build_lines(s, build_id, part_id)
    label = reference
    _activity(
        s,
        "create_build",
        f"Created {label}: build {quantity}x {part.description}",
        [("build", build_id, label), ("part", part_id, part.sku)],
    )


@_write
def edit_build(
    s: Session,
    build_id: int,
    quantity: int,
    reference: str = "",
    status: str = "",
    description: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
) -> None:
    if quantity <= 0:
        raise InventoryError("build quantity must be positive")
    build = get_build(s, build_id)
    already = produced_qty(s, build_id)
    if quantity < already:
        raise InventoryError(
            f"build {build_id}: quantity {quantity} below {already} already produced"
        )
    # status transition rules: Complete only when fully produced; once anything
    # is produced the order can only be Production or Complete (no reverting to
    # Draft/Pending/Cancelled).
    if status == BuildStatus.COMPLETE and already != quantity:
        raise InventoryError(
            f"build {build_id}: cannot complete, produced {already} of {quantity}"
        )
    started_states = (BuildStatus.PRODUCTION, BuildStatus.COMPLETE)
    if already > 0 and status not in started_states:
        raise InventoryError(
            f"build {build_id}: {already} already produced; status must be "
            "Production or Complete"
        )
    # a build always has a code (create_build defaults one); an omitted or
    # blanked reference keeps the stored one rather than clearing it
    reference = reference.strip() or build.reference
    _check_reference_free(s, BuildOrder, reference, build_id)
    build.quantity = quantity
    build.reference = reference
    build.status = status
    build.description = description
    build.start_date = start_date
    build.end_date = end_date


def produced_qty(s: Session, build_id: int) -> float:
    """Total assembly units this build produced, whatever became of them since.

    Counts every row the build produced, not just the Available ones: output
    sold on or consumed by a later build is still output, and filtering by
    status made a finished build's production appear to shrink over time.

    Imported builds also carry `imported_produced`, InvenTree's own count: it
    deletes fully-used stock, so surviving rows under-report history (most
    migrated builds would otherwise read 0). Take whichever is larger -- the
    baseline for what was already made, live rows once the app produces more."""
    from_stock = (
        s.scalar(
            select(func.coalesce(func.sum(StockItem.count), 0.0)).where(
                StockItem.build_id == build_id,
            )
        )
        or 0.0
    )
    return max(from_stock, float(get_build(s, build_id).imported_produced))


@_write
def produce_build(s: Session, build_id: int, quantity: int) -> None:
    """Produce `quantity` assembly units from a build order, consuming the BOM
    components FIFO for those units, in one transaction. Consumption never
    destroys stock: a source Available item's count drops by the amount taken
    and a matching STOCK_CONSUMED item (stamped build_id) is split off, so
    provenance and the price it was bought at stay traceable. Produced stock is
    one new Available item stamped build_id. When cumulative production reaches
    the order quantity the status flips to Complete.

    Shortages do not block: a short component is drained to zero and the missing
    quantity becomes a debt -- a consumed row at the part's estimate (so the
    assembly is costed in full) plus a NEGATIVE Available row stamped with this
    build, which nets out of on-hand counts and the stock value report. Booking
    the missing parts on a PO later settles that debt and reprices the
    consumption to what was actually paid. Virtual components are unlimited:
    never consumed."""
    build = get_build(s, build_id)
    if build.status == BuildStatus.COMPLETE:
        raise InventoryError(f"build order {build_id} is already complete")
    if quantity <= 0:
        raise InventoryError("produce quantity must be positive")
    remaining = build.quantity - produced_qty(s, build_id)
    if quantity > remaining:
        raise InventoryError(
            f"build {build_id}: producing {quantity} exceeds remaining {remaining}"
        )
    # the snapshot taken when the build was created, not the part's live BOM:
    # editing the BOM must not retroactively change what an open build consumes
    bom = build_lines_for(s, build_id)
    if not bom:
        raise InventoryError(f"build {build_id} has no component lines to build")

    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    for _line_id, component_id, _sku, _desc, qty, virtual, price, _partial in bom:
        if virtual:
            # unlimited stock, so nothing is drawn down -- but the build did
            # use it (labour), and the cost is real. Record a consumed row at
            # the snapshot rate so it shows in the consumed table and counts in
            # build_unit_cost. STOCK_CONSUMED keeps it out of the stock value
            # report, which totals Available rows only.
            s.add(
                StockItem(
                    id=next_id,
                    count=qty * quantity,
                    part_id=component_id,
                    consumed_by_build_id=build_id,
                    status=STOCK_CONSUMED,
                    unit_price=price,
                    price_basis=PriceBasis.VIRTUAL
                    if price is not None
                    else PriceBasis.NONE,
                )
            )
            next_id += 1
            continue
        need = qty * quantity
        items = s.scalars(
            select(StockItem)
            .where(
                StockItem.part_id == component_id,
                StockItem.status == STOCK_AVAILABLE,
                StockItem.count > 0,  # never "consume" an outstanding debt row
            )
            .order_by(StockItem.id)
        ).all()
        for item in items:
            if need <= 0:
                break
            take = min(item.count, need)
            item.count -= take
            need -= take
            s.add(
                StockItem(
                    id=next_id,
                    count=take,
                    part_id=component_id,
                    po_id=item.po_id,  # keep the source's price provenance
                    # the source's producing build carries over (this split is
                    # the same stock); the build eating it is the consumer
                    build_id=item.build_id,
                    consumed_by_build_id=build_id,
                    status=STOCK_CONSUMED,
                    # inherit the source's resolved price: what this stock was
                    # actually worth when it went into the build
                    unit_price=item.unit_price,
                    price_basis=item.price_basis,
                    price_po_id=item.price_po_id,
                )
            )
            next_id += 1
        # FIFO left need > 0: stock ran short. The build used these parts
        # anyway, so record them consumed at the part's estimate (so
        # build_unit_cost costs the assembly in full, as an estimate rather
        # than a floor) and carry the debt as a negative Available row. When
        # the parts arrive, book_po_line settles the debt and reprices the
        # consumed row to what was actually paid.
        if need > 1e-9:
            price = get_part(s, component_id).estimated_price
            basis = PriceBasis.ESTIMATE if price is not None else PriceBasis.NONE
            # consumed row first, debt row second, always adjacent ids:
            # _settle_stock_debt pairs them by `debt.id - 1`
            for count in (need, -need):
                s.add(
                    StockItem(
                        id=next_id,
                        count=count,
                        part_id=component_id,
                        consumed_by_build_id=build_id,
                        status=STOCK_CONSUMED if count > 0 else STOCK_AVAILABLE,
                        unit_price=price,
                        price_basis=basis,
                    )
                )
                next_id += 1

    s.add(
        StockItem(
            id=next_id,
            count=quantity,
            part_id=build.part_id,
            build_id=build_id,
            status=STOCK_AVAILABLE,
        )
    )
    s.flush()
    # consumed rows already carry the price they were worth going in. Reprice
    # every Available row of this build (not just the new one): each batch is
    # costed from the build's cumulative consumption, so earlier output moves
    # when more is consumed.
    for item in s.scalars(
        select(StockItem).where(
            StockItem.build_id == build_id, StockItem.status == STOCK_AVAILABLE
        )
    ):
        refresh_stock_price(s, item)
    part = get_part(s, build.part_id)
    label = build.reference or f"BO-{build_id}"
    _activity(
        s,
        "produce_build",
        f"{label}: produced {quantity}x {part.description}",
        [
            ("build", build_id, label),
            ("stock", next_id, f"{quantity}x {part.description}"),
        ],
    )
    if quantity >= remaining:
        build.status = BuildStatus.COMPLETE


# -- demand -----------------------------------------------------------------


def part_demand(s: Session) -> dict[int, tuple[float, float]]:
    """Per part: (units still needed by planned builds, units still to be
    received on open POs). Two grouped queries, not one per part.

    A build counts once it is Pending -- planned is what you buy against; Draft
    is the scratchpad status and asks for nothing. Its need is its snapshot's
    per-unit quantity times the units it has left to produce; a PO line's is the
    packs still outstanding times pack_qty. Virtual components hold no stock, so
    they are never needed."""
    needed: dict[int, float] = {}
    for build_id, part_id, per_unit, qty in s.execute(
        select(
            BuildLine.build_id,
            BuildLine.component_part_id,
            BuildLine.quantity,
            BuildOrder.quantity,
        )
        .join(BuildOrder, BuildLine.build_id == BuildOrder.id)
        .join(Part, BuildLine.component_part_id == Part.id)
        .where(
            BuildOrder.status.in_((BuildStatus.PENDING, BuildStatus.PRODUCTION)),
            Part.virtual.is_(False),
        )
    ):
        # ponytail: produced_qty per build inside the loop; open builds are few.
        # Group stock by build_id if that stops being true.
        remaining = qty - produced_qty(s, build_id)
        if remaining > 0:
            needed[part_id] = needed.get(part_id, 0.0) + per_unit * remaining

    incoming: dict[int, float] = {}
    for part_id, units in s.execute(
        select(
            SupplierPart.part_id,
            func.sum((POLine.quantity - POLine.received) * SupplierPart.pack_qty),
        )
        .join(POLine, POLine.supplier_part_id == SupplierPart.id)
        .join(PurchaseOrder, POLine.po_id == PurchaseOrder.id)
        .where(
            PurchaseOrder.status.not_in(
                (
                    POStatus.CANCELLED,
                    POStatus.COMPLETE,
                    POStatus.RETURNED,
                    POStatus.LOST,
                )
            ),
            POLine.quantity > POLine.received,
        )
        .group_by(SupplierPart.part_id)
    ):
        incoming[part_id] = units

    return {
        pid: (needed.get(pid, 0.0), incoming.get(pid, 0.0))
        for pid in set(needed) | set(incoming)
    }
