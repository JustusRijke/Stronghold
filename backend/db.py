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
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

import crypto
from models import (
    BUILD_STATUS_CODES,
    EXTRAS_DESCRIPTION,
    EXTRAS_WC_LINE_ID,
    PO_STATUS_CODES,
    PRICE_BASIS_CODES,
    SALES_ORDER_STATUS_CODES,
    SO_DEAD_STATUSES,
    STOCK_AVAILABLE,
    STOCK_CONSUMED,
    STOCK_STATUS_CODES,
    Activity,
    Base,
    BomLine,
    Booking,
    BuildLine,
    BuildOrder,
    BuildStatus,
    EnumCode,
    Part,
    PartSku,
    POLine,
    POStatus,
    PriceBasis,
    PurchaseOrder,
    SalesOrder,
    SalesOrderLine,
    SalesOrderLinePart,
    Setting,
    StockItem,
    StockStatus,
    Supplier,
    SupplierPart,
    build_ref,
    po_ref,
    so_ref,
)
from sqlalchemy import create_engine, event, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from version import RELEASE_VERSION, SCHEMA_VERSION

_log = logging.getLogger(__name__)

_engine = None
_export_dir = None
_legacy_file = None  # a pre-split single inventory.sql, replayed once
_export_enabled = True
_auto_commit = False
_committed_activity_id = None
_db_path = None

# domain settings: data, editable in the GUI, stored in the settings table
# ponytail: all values are strings; add typed casting back when a non-str setting appears
DOMAIN_DEFAULTS = {
    "gui.title": "Stronghold",
    # stocktake reason suggestions, comma separated. Split by direction: finding
    # stock and losing it have little vocabulary in common.
    "stocktake.add_reasons": "Found,Refurbished/repaired,Returned by customer,Unknown",
    "stocktake.subtract_reasons": "Damaged,Warranty claim by customer,Lost,Unknown",
    # extra order statuses that raise no demand, comma separated (slugs). The
    # WooCommerce core dead statuses (models.SO_DEAD_STATUSES) always count as
    # dead and are not listed here; this is for a store's own additions, e.g. a
    # quote status from an order-proposal plugin.
    "sales.no_demand_statuses": "",
    # "true" lifts the order status transition rules (so a cancelled order can
    # be un-cancelled) and lets stock counts be typed in directly.
    "expert.mode": "false",
    # The WooCommerce store sales orders are imported from. The url is a plain
    # setting; the key and secret are credentials -- see SECRET_SETTINGS.
    "woocommerce.url": "",
    "woocommerce.key": "",
    "woocommerce.secret": "",
}

# Settings holding a credential. Their value is encrypted at rest (crypto.py)
# because the settings table is exported to the git-tracked .sql, and it is
# never logged or sent to the frontend.
#
# Declared here in code rather than as a column on the row: a flag stored in
# the data file could be flipped off by hand, and the next export would write
# the plaintext. Add a key here to make it a credential.
SECRET_SETTINGS = frozenset({"woocommerce.key", "woocommerce.secret"})

EXPERT_MODE_KEY = "expert.mode"


def expert_mode() -> bool:
    return get_setting(EXPERT_MODE_KEY) == "true"


# Version stamps. These live in the settings table because that is the one
# place a value survives the export/replay roundtrip, but they are app
# metadata, not domain settings: not in DOMAIN_DEFAULTS, so get_setting /
# set_setting reject them and they never appear on the settings page.
# The store's order statuses, slug -> label, as JSON. Cached from WooCommerce on
# every import (see import_woocommerce): the set is whatever the store's plugins
# registered, so it cannot be hardcoded, and the labels are in the store's own
# language. Not a DOMAIN_DEFAULTS setting -- it is imported data, not something
# to hand-edit on the settings page.
SO_STATUS_LABELS_KEY = "sales.status_labels"

SCHEMA_VERSION_KEY = "schema.version"
APP_VERSION_KEY = "app.version"
VERSION_KEYS = frozenset({SCHEMA_VERSION_KEY, APP_VERSION_KEY})


class InventoryError(Exception):
    """A write cannot apply to the current state."""


class DataVersionError(Exception):
    """The data file was written by a Stronghold too new to read it safely."""


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def suspend_export() -> None:
    """Stop exporting after every write, for a bulk load that would otherwise
    rewrite the whole file per row. The loader MUST finish with
    `export(force=True)` -- until it does, nothing is being persisted."""
    global _export_enabled
    _export_enabled = False


def init(sql_path: Path, auto_commit: bool = False) -> None:
    """Open the data in `sql_path`: a directory of per-table .sql files (or a
    single pre-split .sql, migrated to that layout on the way in). Those files
    are the truth (tracked in git, restorable by checking out an older commit);
    SQLite is only how we query them, so the .db is rebuilt from scratch in a
    temp directory on every startup and never has to be looked after."""
    global _engine, _export_dir, _legacy_file, _db_path, _export_enabled
    global _auto_commit, _committed_activity_id
    # one path setting, two shapes: a directory of per-table .sql files, or --
    # for data written before the split -- a single .sql, replayed once and
    # re-exported into a directory beside it.
    _legacy_file = sql_path if sql_path.is_file() else None
    _export_dir = sql_path.parent / sql_path.stem if _legacy_file else sql_path
    _export_enabled = True  # a previous suspend_export must not leak in here
    _auto_commit = auto_commit
    _committed_activity_id = None
    if auto_commit and Path(__file__).parent.parent in sql_path.resolve().parents:
        raise ValueError(
            f"db.auto_commit is on but the data file lives inside the Stronghold "
            f"app repo ({sql_path}): the data belongs in a repo of its own, or "
            f"every write would commit to the application's history"
        )
    _db_path = _working_db_path(sql_path)
    _db_path.unlink(missing_ok=True)
    _engine = create_engine(f"sqlite:///{_db_path}")
    event.listen(_engine, "connect", _enable_foreign_keys)
    Base.metadata.create_all(_engine)
    # before the replay: startup ends in an export that overwrites these files
    _commit_pending_changes()
    was = None
    if _legacy_file or any(_export_dir.glob("*.sql")):
        _import_sql()
        was = _read_stamps()
        _migrate()
        _rebuild_sku_indexes()
    _export_dir.mkdir(parents=True, exist_ok=True)
    _stamp_versions()
    # startup's export is not a domain write, so it says what it actually did
    startup_message(_startup_commit_message(was))
    try:
        export()
    finally:
        startup_message(None)


def _working_db_path(sql_path: Path) -> Path:
    """A per-data-file scratch database under the system temp directory. Named
    after the .sql's full path so two datasets never share one working copy."""
    digest = hashlib.sha256(str(sql_path.resolve()).encode()).hexdigest()[:12]
    directory = Path(tempfile.gettempdir()) / "stronghold"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{sql_path.stem}-{digest}.db"


def _import_sql() -> None:
    """Replay the exported .sql into the freshly created (empty) database.

    One file per table, replayed in dependency order -- though order does not
    actually matter: this raw sqlite3 connection has foreign_keys OFF (the ON
    pragma is attached to the SQLAlchemy engine only), so nothing is checked
    until the app queries through that engine.

    The tables already exist in their *current* shape (create_all ran first), so
    a column an older file does not have simply takes its default. A column the
    file *does* have and we have since dropped is the hard case: its INSERTs
    would fail outright, killing startup before _migrate could fix anything. So
    every dropped column is temporarily put back before the replay and dropped
    again by its migration step -- see _DROPPED_COLUMNS.

    A unique index has the same shape of problem from the other side: a file
    written before parts.sku became unique may hold rows that violate it (272
    parts sharing an empty sku, in the dataset this was built for), and the
    replay would fail before _migrate could clear them. So the index comes down
    here and migration 5 puts it back once the data is clean."""
    with sqlite3.connect(_db_path) as conn:
        for index in _SKU_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        for table, column, ddl_type in _dropped_columns():
            # unconditional: the stamp is only readable once the data is in.
            # Harmless for a current file -- the column stays empty and its
            # migration step (a no-op for that file) drops it again.
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        for table, ddl in _dropped_tables().items():
            # same deal one level up: create_all no longer knows these, so they
            # are rebuilt empty here to catch an older file's INSERTs.
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({ddl})")
        if _legacy_file:
            conn.executescript(_legacy_file.read_text(encoding="utf-8"))
            _log.info(
                "loaded the single-file %s; re-exporting it as one .sql per "
                "table under %s. The old file is left untouched and can be "
                "deleted once you are happy with the new layout.",
                _legacy_file,
                _export_dir,
            )
            return
        names = [t.name for t in Base.metadata.sorted_tables]
        names += list(_dropped_tables())  # an older file may still carry these
        for name in names:
            path = _table_path(name)
            if path.exists():
                conn.executescript(path.read_text(encoding="utf-8"))
    _log.info("loaded %s (working copy: %s)", _export_dir, _db_path)


# The unique sku indexes, dropped before a replay and rebuilt afterwards (see
# _import_sql and _rebuild_sku_indexes). Named in the models' __table_args__.
# parts.sku is unique globally; supplier_parts.sku only within one supplier.
_SKU_INDEXES = {
    "ix_parts_sku_unique": "parts (sku)",
    "ix_supplier_parts_sku_unique": "supplier_parts (supplier_id, sku)",
}


def _dropped_columns() -> list[tuple[str, str, str]]:
    """Every (table, column, type) removed by a migration, newest step last."""
    return [
        (table, column, ddl_type)
        for step in sorted(_DROPPED_COLUMNS)
        for table, column, ddl_type in _DROPPED_COLUMNS[step]
    ]


def _dropped_tables() -> dict[str, str]:
    """Every {table: columns DDL} removed by a migration, newest step last."""
    return {
        table: ddl
        for step in sorted(_DROPPED_TABLES)
        for table, ddl in _DROPPED_TABLES[step].items()
    }


def session() -> Session:
    """Read access; writes go through the functions below."""
    return Session(_engine)


# -- data versioning --------------------------------------------------------

# Columns a migration removed, keyed by the SCHEMA_VERSION that removed them,
# as (table, column, SQL type). _import_sql puts them back before the replay so
# an older file's INSERTs still fit; the migration step below drops them again.
# The type only has to hold the old values long enough to be discarded, so the
# permissive one is right -- a NOT NULL here would reject the current file,
# whose INSERTs no longer carry the column.
_DROPPED_COLUMNS = {
    2: [
        ("purchase_orders", "reference", "VARCHAR"),
        ("build_orders", "reference", "VARCHAR"),
    ],
    11: [("sales_orders", "wc_order_id", "INTEGER")],
}

# Whole tables a migration removed, keyed by the SCHEMA_VERSION that removed
# them. The column scaffold above is not enough for these: _import_sql replays
# one file per *declared* table, so an undeclared table's .sql would not be read
# at all and its data would vanish silently. Each is recreated empty before the
# replay, read by its migration step, and dropped there. Types are permissive --
# they only have to hold the old values long enough to be migrated out.
_DROPPED_TABLES = {
    14: {
        "product_sku_parts": (
            "id INTEGER, sku VARCHAR, part_id INTEGER, quantity FLOAT"
        )
    },
}


def _drop_columns(s: Session, step: int) -> None:
    """Discard the columns `step` removed. The data is not migrated anywhere:
    the order codes these held were always a copy of the pk, so models.po_ref
    reproduces them exactly."""
    for table, column, _ in _DROPPED_COLUMNS[step]:
        s.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def _code_for(codes: dict, member) -> int:
    """The stored int for an enum member. Inverts the models code map so the
    migration cannot drift from what EnumCode actually writes."""
    return next(k for k, v in codes.items() if v == member)


# What each enum column held as text before v3, mapped to the code it stores
# now. Stock status appears twice over: the 1.0 display prose, and the enum's
# own value, which was briefly what went on disk.
_V3_TEXT_TO_CODE: dict[tuple[str, str], dict[str, int]] = {
    ("stock_items", "status"): {
        "Available": _code_for(STOCK_STATUS_CODES, StockStatus.AVAILABLE),
        "Consumed by build order": _code_for(STOCK_STATUS_CODES, StockStatus.CONSUMED),
        **{s.value: _code_for(STOCK_STATUS_CODES, s) for s in StockStatus},
    },
    ("stock_items", "price_basis"): {
        b.value: _code_for(PRICE_BASIS_CODES, b) for b in PriceBasis
    },
    ("purchase_orders", "status"): {
        p.value: _code_for(PO_STATUS_CODES, p) for p in POStatus
    },
    ("build_orders", "status"): {
        b.value: _code_for(BUILD_STATUS_CODES, b) for b in BuildStatus
    },
}


def _to_v3(s: Session) -> None:
    """Store the enum columns as ints rather than text.

    SQLite is dynamically typed, so the replayed rows sit as text in what are
    now INTEGER columns; this rewrites them in place. Anything unrecognised is
    left alone and logged -- a value we cannot map is bad data, and silently
    zeroing it would lose a PO's state or make stock vanish from the on-hand
    sums. It surfaces immediately either way: reading such a row fails."""
    for (table, column), mapping in _V3_TEXT_TO_CODE.items():
        for old, code in mapping.items():
            s.execute(
                text(f"UPDATE {table} SET {column} = :code WHERE {column} = :old"),  # noqa: S608
                {"code": code, "old": old},
            )
        # "" is the no-status-yet default on POs and builds; these columns are
        # NOT NULL, so it gets EnumCode's reserved code rather than a NULL
        s.execute(
            text(f"UPDATE {table} SET {column} = :unset WHERE {column} = ''"),  # noqa: S608
            {"unset": EnumCode.UNSET},
        )
        left = (
            s.execute(
                text(
                    f"SELECT DISTINCT {column} FROM {table} "  # noqa: S608
                    f"WHERE typeof({column}) = 'text'"
                )
            )
            .scalars()
            .all()
        )
        if left:
            _log.warning("%s.%s: leaving unmappable values %s", table, column, left)


# Steps that bring an older data file up to date, keyed by the SCHEMA_VERSION
# they produce: {2: _to_v2} runs when a version-1 file is opened by a version-2
# app. They transform *replayed data*, not the schema -- create_all already
# built the current tables before the replay, which is also why Alembic buys us
# nothing here. The exception is a dropped column, which needs both halves:
# an _import_sql scaffold and a step here to take it down again.
def _rebuild_sku_indexes() -> None:
    """Put back the unique indexes _import_sql dropped for the replay. Runs on
    every startup, not just a migration: the drop is unconditional, so a
    current file would otherwise come up without its constraints. By now the
    data satisfies them -- an older file was cleaned by _clean_skus, and
    anything written since was checked on the way in by _validate_sku /
    _validate_supplier_sku."""
    with Session(_engine) as s, s.begin():
        for index, target in _SKU_INDEXES.items():
            s.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {target}"))


def _to_v5(s: Session) -> None:
    """The sku columns became optional and unique. parts.sku is unique
    globally; supplier_parts.sku only within its supplier."""
    _clean_skus(s, "parts", None)
    _clean_skus(s, "supplier_parts", "supplier_id")


def _clean_skus(s: Session, table: str, scope: str | None) -> None:
    """Make table.sku fit a unique index: blanks become NULL (those never
    clash), and a sku already taken within the same
    scope is kept on the lowest id and cleared on the rest -- the index rebuilt
    right after would otherwise reject the data outright and kill startup.
    scope is the column uniqueness is per (supplier_id), or None for globally
    unique. Clearing is reported: it drops values the user typed."""
    columns = f"id, sku, {scope}" if scope else "id, sku"
    seen: set[tuple] = set()
    for row in s.execute(text(f"SELECT {columns} FROM {table} ORDER BY id")).all():  # noqa: S608
        row_id, sku = row[0], row[1]
        keep = _normalise_sku(sku)
        key = (row[2] if scope else None, keep)
        if keep is not None and key in seen:
            _log.warning(
                "%s %d: sku %r is already used%s; clearing it",
                table,
                row_id,
                keep,
                f" by an earlier row with the same {scope}" if scope else "",
            )
            keep = None
        if keep is not None:
            seen.add(key)
        if keep != sku:
            s.execute(
                text(f"UPDATE {table} SET sku = :sku WHERE id = :id"),  # noqa: S608
                {"sku": keep, "id": row_id},
            )


def _to_v7(s: Session) -> None:
    """Date pre-v7 stock from the order it came from. There is no exact
    timestamp to recover, so this is the approximate date the stock log already
    showed (api._stock_log_entries), promoted onto the column, and in that
    function's precedence: what happened *to* the stock wins over where it came
    from, so a consumed row is dated by the order that ate it, not the PO it was
    bought on.

    A row none of those can date stays NULL, and the UI shows it blank. That is
    deliberate: "unknown" is the truth about such a row, and stamping it with
    the migration's own "now" would invent a creation date that the data would
    then carry forever as if it were real (26 rows of 4009 in the dataset this
    was written for)."""
    s.execute(
        text("""
        UPDATE stock_items SET created_at = COALESCE(
            (SELECT b.start_date FROM build_orders b WHERE b.id = consumed_by_build_id),
            (SELECT o.date_created FROM sales_orders o WHERE o.id = consumed_by_so_id),
            stocktake_at,
            (SELECT b.start_date FROM build_orders b WHERE b.id = build_id),
            (SELECT p.start_date FROM purchase_orders p WHERE p.id = po_id)
        )
        """)
    )


def _to_v10(s: Session) -> None:
    """sales_orders.status went from an EnumCode int back to WooCommerce's slug.

    A store's statuses are not a closed set -- plugins register their own -- so
    the int codes could only ever hold the seven we hardcoded and every other
    status imported blank. The replayed rows are still ints in what is now a
    text column; this maps them back to slugs. An unmapped value is left alone
    and logged: it is bad data, and blanking it would lose the order's state."""
    for code, status in SALES_ORDER_STATUS_CODES.items():
        s.execute(
            text("UPDATE sales_orders SET status = :slug WHERE status = :code"),
            {"slug": str(status), "code": code},
        )
    # EnumCode's reserved unset code: an order whose status we could not map
    s.execute(
        text("UPDATE sales_orders SET status = '' WHERE status = :unset"),
        {"unset": EnumCode.UNSET},
    )
    left = (
        s.execute(
            text(
                "SELECT DISTINCT status FROM sales_orders "
                "WHERE typeof(status) != 'text'"
            )
        )
        .scalars()
        .all()
    )
    if left:
        _log.warning("sales order statuses left unmapped: %s", sorted(left))


def _to_v11(s: Session) -> None:
    """sales_orders.id became the WooCommerce order id (was a local max+1 with
    wc_order_id alongside). The orders are one-to-one, so the second id was only
    a second number to show.

    No old order is renumbered: nothing had been imported when this landed, so
    the sales tables are simply emptied and re-imported from WooCommerce. If a
    file ever turns up with orders in it, they come back on the next import --
    only the manual part links are lost with them."""
    s.execute(text("DELETE FROM sales_order_line_parts"))
    s.execute(text("DELETE FROM sales_order_lines"))
    s.execute(text("DELETE FROM sales_orders"))
    s.execute(text("UPDATE stock_items SET consumed_by_so_id = NULL"))


def _to_v14(s: Session) -> None:
    """A sales sku became a label on a part instead of carrying its own part
    list. A sku that named several parts (or one part more than once per sale)
    has no home under the new shape, so it gets the assembly it always was:
    the list moves into a BOM, and the sku points at that.

    Skus with an identical list share one assembly -- that is the whole point of
    the change, and in the dataset it was written for 21 such skus collapse onto
    9 assemblies. The new parts are reported: they need naming by hand."""
    rows = s.execute(
        text("SELECT sku, part_id, quantity FROM product_sku_parts ORDER BY id")
    ).all()
    groups: dict[str, list[tuple[int, float]]] = {}
    for sku, part_id, quantity in rows:
        groups.setdefault(sku, [])
        if part_id is not None:  # a part-less row is the ignore marker
            groups[sku].append((part_id, quantity))

    # one assembly per distinct part list, shared by every sku naming it
    assemblies: dict[frozenset, int] = {}
    next_part = (s.scalar(select(func.max(Part.id))) or 0) + 1
    next_bom = (s.scalar(select(func.max(BomLine.id))) or 0) + 1
    next_link = 1  # part_skus is new, so its ids start clean
    for sku, parts in sorted(groups.items()):
        if len(parts) == 1 and parts[0][1] == 1:
            part_id = parts[0][0]  # a plain one-to-one mapping, no assembly
        elif not parts:
            part_id = None  # ignored, as before
        else:
            key = frozenset(parts)
            if key not in assemblies:
                s.add(
                    Part(
                        id=next_part,
                        description=f"{sku} (assembly built by the schema 14 "
                        f"migration -- rename me)",
                        assembly=True,
                        purchasable=False,
                    )
                )
                s.flush()  # the part must land before its BOM lines point at it
                for component, quantity in parts:
                    s.add(
                        BomLine(
                            id=next_bom,
                            parent_part_id=next_part,
                            component_part_id=component,
                            quantity=quantity,
                        )
                    )
                    next_bom += 1
                _log.warning(
                    "sku %s mapped to %d part(s); built part %d to hold that "
                    "list -- give it a proper name and sku",
                    sku,
                    len(parts),
                    next_part,
                )
                assemblies[key] = next_part
                next_part += 1
            part_id = assemblies[key]
        s.add(PartSku(id=next_link, sku=sku, part_id=part_id))
        next_link += 1
    s.execute(text("DROP TABLE product_sku_parts"))


_MIGRATIONS = {
    2: lambda s: _drop_columns(s, 2),
    3: _to_v3,
    # 4 only added tables and a nullable column: create_all already built them
    # and an older file simply has no INSERTs for them. The step still has to
    # exist -- _migrate walks every version up to SCHEMA_VERSION and would
    # KeyError on the gap.
    4: lambda s: None,
    5: _to_v5,
    # 6 only added a nullable column -- see the note on step 4.
    6: lambda s: None,
    7: _to_v7,
    # 8 only added a table -- see the note on step 4.
    8: lambda s: None,
    # 9 only added a nullable column -- see the note on step 4.
    9: lambda s: None,
    10: _to_v10,
    11: lambda s: (_to_v11(s), _drop_columns(s, 11)),
    # 12 only added a column -- see the note on step 4.
    12: lambda s: None,
    # 13 only relaxed two NOT NULLs -- create_all built the new shape and every
    # replayed row still satisfies it. See the note on step 4.
    13: lambda s: None,
    14: _to_v14,
}


def _read_stamps() -> tuple[int, str | None]:
    """The schema version and app version the data file carried when opened."""
    with session() as s:
        app = s.get(Setting, APP_VERSION_KEY)
        return _read_schema_version(s), app.value if app else None


def _startup_commit_message(was: tuple[int, str | None] | None) -> str:
    """Say what startup actually changed. Startup rewrites the file whether or
    not anything moved, so this is also the message on a no-op commit -- git
    just finds nothing staged and skips it."""
    if was is None:
        return f"Created by Stronghold {RELEASE_VERSION} (data schema {SCHEMA_VERSION})"
    schema, app = was
    if schema != SCHEMA_VERSION:
        return (
            f"Migrated data from schema {schema} to {SCHEMA_VERSION} "
            f"(Stronghold {RELEASE_VERSION})"
        )
    if app is None:
        return (
            f"Recorded the Stronghold version in the data file "
            f"(Stronghold {RELEASE_VERSION}, data schema {SCHEMA_VERSION})"
        )
    if app != RELEASE_VERSION:
        return f"Opened with Stronghold {RELEASE_VERSION} (was {app})"
    return f"Opened with Stronghold {RELEASE_VERSION}"


def _read_schema_version(s: Session) -> int:
    """The schema version of the open data. A file written before stamping
    existed has no row: it predates the stamp, so it is version 1."""
    row = s.get(Setting, SCHEMA_VERSION_KEY)
    return int(row.value) if row else 1


def data_schema_version() -> int:
    with session() as s:
        return _read_schema_version(s)


def _stamp_versions() -> None:
    """Record which Stronghold wrote this data. Written straight to the table,
    not via set_setting: that is @_write-decorated and would re-export (and
    re-commit) from inside init, before the caller's own export."""
    with Session(_engine) as s, s.begin():
        for key, value in (
            (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
            (APP_VERSION_KEY, RELEASE_VERSION),
        ):
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            elif row.value != value:
                row.value = value


def _migrate() -> None:
    """Bring the just-replayed data up to SCHEMA_VERSION.

    Older data is upgraded in place and re-exported by init's own export().
    *Newer* data is refused outright: this app would export only the columns it
    knows about, so the first write would silently drop the newer ones -- and
    with auto_commit on, commit that loss over the only copy of the data."""
    with session() as s:
        found = _read_schema_version(s)
    if found == SCHEMA_VERSION:
        return
    if found > SCHEMA_VERSION:
        with session() as s:
            wrote = s.get(Setting, APP_VERSION_KEY)
        raise DataVersionError(
            f"{_legacy_file or _export_dir} holds schema version {found}, but this Stronghold "
            f"({RELEASE_VERSION}) only understands version {SCHEMA_VERSION}. It "
            f"was written by Stronghold {wrote.value if wrote else 'unknown'}; "
            f"install that version or newer. Refusing to start: opening it here "
            f"would drop the data this version does not know about."
        )
    for step in range(found + 1, SCHEMA_VERSION + 1):
        _log.info("migrating %s: schema %d -> %d", _export_dir, step - 1, step)
        with Session(_engine) as s, s.begin():
            _MIGRATIONS[step](s)


def no_demand_statuses() -> tuple[str, ...]:
    """Every status that asks nothing of purchasing: WooCommerce's own dead ones
    plus whatever the user listed (a quote status, say). A plugin can invent any
    status, so the extras are a setting rather than a constant."""
    extra = [
        v.strip()
        for v in get_setting("sales.no_demand_statuses").split(",")
        if v.strip()
    ]
    return tuple(SO_DEAD_STATUSES) + tuple(extra)


def so_status_labels() -> dict[str, str]:
    """The store's order statuses, slug -> label. Empty until the first import."""
    with session() as s:
        row = s.get(Setting, SO_STATUS_LABELS_KEY)
    return json.loads(row.value) if row else {}


def set_so_status_labels(s: Session, labels: dict[str, str]) -> None:
    """Cache what the store calls each status. Called inside the import's own
    transaction, so it logs nothing of its own -- the import's activity row
    already says what happened."""
    value = json.dumps(labels, sort_keys=True)
    row = s.get(Setting, SO_STATUS_LABELS_KEY)
    if row is None:
        s.add(Setting(key=SO_STATUS_LABELS_KEY, value=value))
    else:
        row.value = value


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
    """Write the database as one .sql file per table under the data directory;
    one INSERT statement per row, readable, diffable, git-friendly. These files
    are the truth -- the .db is rebuilt from them at startup -- so each is
    written atomically. NULL and derived columns are left out of each INSERT;
    the schema defaults cover them on restore.

    Only files whose content actually changed are rewritten, so a stock edit
    leaves parts.sql alone and git shows a one-file diff.

    A file for a table the models no longer declare is deleted. Both the replay
    and this loop walk the declared tables, so such a file is otherwise never
    read and never rewritten -- it would sit in the data directory (and its git
    history) forever, looking like current data.

    `force` writes even while a bulk load has export suspended -- that is how
    the loader persists its result at the end."""
    if not _export_enabled and not force:
        return
    _export_dir.mkdir(parents=True, exist_ok=True)
    with Session(_engine) as s:
        conn = s.connection()
        for table in Base.metadata.sorted_tables:
            _write_table(conn, table)
    _drop_stale_files()
    _commit_export()


def _drop_stale_files() -> None:
    """Remove exported files for tables that no longer exist in the models."""
    current = {f"{table.name}.sql" for table in Base.metadata.sorted_tables}
    for path in _export_dir.glob("*.sql"):
        if path.name not in current:
            _log.info("removing %s: its table is no longer part of the schema", path)
            path.unlink()


def _write_table(conn, table) -> None:
    """One table's CREATE + INSERTs, written atomically to <dir>/<table>.sql."""
    lines = [
        (
            f"-- Stronghold data: the {table.name} table. Restore the whole "
            f"dataset into a fresh (empty) database: cat *.sql | sqlite3 inventory.db"
        ),
        # For the human opening this file. The authority is the row in
        # settings.sql (a comment cannot survive a restore).
        f"-- Written by Stronghold {RELEASE_VERSION}, data schema version {SCHEMA_VERSION}.",
        str(CreateTable(table, if_not_exists=True).compile(_engine)).strip() + ";",
    ]
    skip = _DERIVED_COLUMNS.get(table.name, frozenset())
    names = [c.name for c in table.columns]
    # S608: table/column names come from our own model metadata, never from
    # user input -- there is no injection vector here.
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
    content = "\n".join(lines) + "\n"
    path = _table_path(table.name)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return  # unchanged: leave it alone so git sees only what moved
    # atomic: a crash mid-write must not truncate the only copy of the data
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _table_path(name: str) -> Path:
    return _export_dir / f"{name}.sql"


_UNCOMMITTED_MESSAGE = (
    "Uncommitted changes to the data file, committed at startup before "
    "reloading it (edited by hand, or written by a session that did not "
    "commit). Roll back to the previous commit to undo them."
)

# Startup's own export. It is not a domain write, so the newest Activity row
# describes some *earlier* session's change, not this one -- using it produced
# a duplicate "Received PO-0289 into stock" whose only content was the version
# stamp. Set while init runs and cleared after, so writes go back to their own
# activity messages.
_startup_message = None

_NO_ACTIVITY_MESSAGE = (
    "(no activity log record for this change -- the write that caused it does "
    "not call db._activity; worth reporting to the developers)"
)


def startup_message(message: str | None) -> None:
    """Name the commit for an export that is not a domain write. Startup uses
    it for the version stamp and the price refresh, which would otherwise reuse
    an unrelated earlier Activity row or commit as "no activity log record"."""
    global _startup_message
    _startup_message = message


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run git in the data directory itself. Failures are the caller's to
    interpret: no repo, nothing staged and no identity are all ordinary here."""
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=_export_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def _commit(message: str) -> None:
    for args in (
        ("add", "--", "."),
        ("commit", "--only", "--message", message, "--", "."),
    ):
        result = _git(*args)
        if result.returncode:
            _log.debug(
                "auto commit skipped: %s", (result.stderr or result.stdout).strip()
            )
            return


def _commit_export() -> None:
    """Commit the exported data file, if it lives in a git repo and
    db.auto_commit is on. The newest activity row is the message; a write that
    logged nothing gets a message saying so, rather than being left uncommitted.

    ponytail: shells out to git and ignores its exit code -- no repo, nothing
    staged, or a missing identity all just mean "no commit today". Inspect the
    output only when someone actually needs to debug it."""
    global _committed_activity_id
    if not _auto_commit:
        return
    with Session(_engine) as s:
        row = s.scalars(select(Activity).order_by(Activity.id.desc()).limit(1)).first()
    # startup is not a domain write: the newest activity row belongs to an
    # earlier session and would misdescribe this commit
    if _startup_message:
        _committed_activity_id = row.id if row else None
        _commit(_startup_message)
        return
    # a row we already used as a message says nothing about *this* write
    fresh = row if row and row.id != _committed_activity_id else None
    message = fresh.message if fresh else _NO_ACTIVITY_MESSAGE
    _committed_activity_id = row.id if row else None
    _commit(message)


def _commit_pending_changes() -> None:
    """Commit whatever is already in the data file before we overwrite it.

    Startup ends in an export that rewrites the file wholesale, so anything
    uncommitted in it -- a hand edit, or a write from a session that died
    before its own commit -- would be destroyed with no way back. Committing
    first costs nothing when there is nothing to commit, and leaves the
    previous state recoverable when there is."""
    if not _auto_commit or not _export_dir.exists():
        return
    if not _git("status", "--porcelain", "--", ".").stdout.strip():
        return  # unchanged, or not a git repo at all
    _log.info("committing uncommitted changes in %s before reload", _export_dir)
    _commit(_UNCOMMITTED_MESSAGE)


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


def _field_changes(row, **new) -> list[str]:
    """'field old -> new' for each attribute the edit actually changes."""
    return [
        f"{k} {getattr(row, k)} -> {v}" for k, v in new.items() if getattr(row, k) != v
    ]


def _supplier_part_label(s: Session, sp: SupplierPart) -> str:
    """Name a supplier part in a log line. Most have neither sku nor their own
    description, so the part's description is the only thing that identifies it."""
    return sp.sku or sp.description or get_part(s, sp.part_id).description


def _activity(s: Session, action: str, message: str, refs: list[tuple]) -> None:
    """Append an activity-log row inside the caller's transaction (so it commits
    atomically with the action). refs: list of (type, id, label) tuples; a None
    label (a part with no sku) is stored as "" so the log never renders "null"."""
    s.add(
        Activity(
            action=action,
            message=message,
            refs=json.dumps(
                [{"type": t, "id": i, "label": l or ""} for t, i, l in refs]
            ),
        )
    )


# -- settings ---------------------------------------------------------------


def get_setting(key: str) -> str:
    if key not in DOMAIN_DEFAULTS:
        raise InventoryError(f"unknown setting '{key}'")
    with session() as s:
        row = s.get(Setting, key)
    if row is None:
        return DOMAIN_DEFAULTS[key]
    if key not in SECRET_SETTINGS:
        return row.value
    # A credential we cannot decrypt (key file missing, replaced, or the row
    # hand-edited) reads as unset, so the app reports "not configured" and the
    # user re-enters it. Raising here would take down every page that reads a
    # setting over a value that is only recoverable by retyping it.
    return crypto.decrypt(row.value) or ""


@_write
def set_setting(s: Session, key: str, value: str) -> None:
    if key not in DOMAIN_DEFAULTS:
        raise InventoryError(f"unknown setting '{key}'")
    row = s.get(Setting, key)
    secret = key in SECRET_SETTINGS
    old = DOMAIN_DEFAULTS[key] if row is None else row.value
    stored = value
    if secret and value:
        stored = crypto.encrypt(value)
        if stored is None:
            raise InventoryError(
                f"cannot store '{key}': no usable secrets key file. See the "
                f"secrets_key_file setting in settings.toml."
            )
    if row is None:
        s.add(Setting(key=key, value=stored))
    else:
        row.value = stored
    if stored == old:
        return  # a no-op patch should not manufacture an activity row
    if secret:
        # the change is worth recording; the credential is not. Note this
        # compares ciphertexts, so re-saving the same secret still logs -- two
        # encryptions of one value differ, and peeking to compare would defeat
        # the point.
        _activity(s, "set_setting", f"Setting {key}: value changed", [])
    else:
        _activity(s, "set_setting", f"Setting {key}: '{old}' -> '{value}'", [])


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


def _normalise_sku(sku: str | None) -> str | None:
    """The value to store: a blank sku means "no sku" and becomes NULL. The
    unique indexes count every NULL as distinct, so any number of parts (or of
    one supplier's parts) may go without one, while a real sku can only be used
    once -- globally for a Part, per supplier for a SupplierPart. Only blanks
    are special-cased: what counts as a placeholder ("N/A", "TBD", ...) is the
    user's own convention, not ours, so such a value is stored as typed and the
    duplicates among it are cleared by _clean_skus with a line in the log."""
    if sku is None:
        return None
    return sku.strip() or None


def _validate_sku(s: Session, sku: str | None, part_id: int | None = None) -> None:
    """Reject a malformed sku, or one another part already uses. part_id is the
    part being written, so re-saving a part's own sku is not a clash."""
    if sku is None:
        return
    if len(sku) > 64 or any(c.isspace() for c in sku):
        raise InventoryError(f"invalid sku {sku!r}: max 64 chars, no whitespace")
    clash = s.scalar(select(Part).where(Part.sku == sku, Part.id != part_id))
    if clash is not None:
        raise InventoryError(
            f"sku {sku!r} is already used by part {clash.id} ({clash.description})"
        )


@_write
def create_part(
    s: Session, part_id: int, sku: str | None, description: str, virtual: bool = False
) -> None:
    sku = _normalise_sku(sku)
    _validate_sku(s, sku)
    if s.get(Part, part_id) is not None:
        raise InventoryError(f"part id {part_id} already exists")
    s.add(Part(id=part_id, sku=sku, description=description, virtual=virtual))
    _activity(
        s, "create_part", f"Created part {description}", [("part", part_id, sku or "")]
    )


@_write
def clone_part(s: Session, part_id: int, new_id: int, description: str) -> None:
    """Copy a part's basics onto a new one: description, the assembly/virtual/
    purchasable flags, a hand-set price and the BOM. Nothing that identifies the
    original or its history -- no sku, supplier parts, stock or orders."""
    src = get_part(s, part_id)
    if s.get(Part, new_id) is not None:
        raise InventoryError(f"part id {new_id} already exists")
    s.add(
        Part(
            id=new_id,
            sku=None,
            description=description,
            assembly=src.assembly,
            virtual=src.virtual,
            purchasable=src.purchasable,
            estimated_price=src.estimated_price if src.virtual else None,
        )
    )
    line_id = (s.scalar(select(func.max(BomLine.id))) or 0) + 1
    for line in s.scalars(select(BomLine).where(BomLine.parent_part_id == part_id)):
        s.add(
            BomLine(
                id=line_id,
                parent_part_id=new_id,
                component_part_id=line.component_part_id,
                quantity=line.quantity,
                note=line.note,
            )
        )
        line_id += 1
    s.flush()
    refresh_part_price(s, new_id)
    _activity(
        s,
        "clone_part",
        f"Cloned part {src.description} as {description}",
        [("part", part_id, src.sku or ""), ("part", new_id, "")],
    )


@_write
def edit_part(s: Session, part_id: int, description: str) -> None:
    part = get_part(s, part_id)
    changes = _field_changes(part, description=description)
    part.description = description
    if changes:
        _activity(
            s,
            "edit_part",
            f"Part {part.description}: {', '.join(changes)}",
            [("part", part_id, part.sku or "")],
        )


@_write
def set_part_sku(s: Session, part_id: int, sku: str | None) -> None:
    sku = _normalise_sku(sku)
    _validate_sku(s, sku, part_id)
    part = get_part(s, part_id)
    changes = _field_changes(part, sku=sku)
    part.sku = sku
    if changes:
        _activity(
            s,
            "set_part_sku",
            f"Part {part.description}: {', '.join(changes)}",
            [("part", part_id, sku or "")],
        )


@_write
def set_part_active(s: Session, part_id: int, active: bool) -> None:
    part = get_part(s, part_id)
    part.active = active
    verb = "Activated" if active else "Deactivated"
    _activity(
        s,
        "set_part_active",
        f"{verb} part {part.description}",
        [("part", part_id, part.sku or "")],
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


def po_totals(s: Session, po_id: int | None = None) -> dict[int, tuple[float, float]]:
    """Per order id: (goods subtotal, delivery factor). The delivery cost is
    split over the lines in proportion to their value, so every line on the
    order scales by the same factor -- nothing to apportion per line and no
    rounding remainder. An order with no lines is absent; read (0.0, 1.0).

    One grouped query, so callers pricing many lines do not go N+1; pass po_id
    for a single order."""
    q = (
        select(
            POLine.po_id,
            func.sum(POLine.quantity * POLine.price),
            PurchaseOrder.delivery_cost,
        )
        .join(PurchaseOrder, POLine.po_id == PurchaseOrder.id)
        .group_by(POLine.po_id)
    )
    if po_id is not None:
        q = q.where(POLine.po_id == po_id)
    return {
        po: (goods, 1.0 + delivery / goods if delivery > 0 and goods > 0 else 1.0)
        for po, goods, delivery in s.execute(q)
    }


# ponytail: module-level cache, valid only for the duration of one
# refresh_all_prices pass -- that pass writes no po_lines/purchase_orders rows,
# so the factors cannot move under it. Deliberately NOT session-scoped: a write
# transaction changes a line price and then reprices in the same session, where
# a cached factor would be the pre-change one.
_factor_cache: dict[int, float] = {}


def _delivery_factor(s: Session, po_id: int) -> float:
    if _factor_cache:
        return _factor_cache.get(po_id, 1.0)
    return po_totals(s, po_id).get(po_id, (0.0, 1.0))[1]


def latest_po_price_source(s: Session, part_id: int) -> tuple[float, int, str] | None:
    """(landed unit price, po id, po reference) from the most recent
    non-cancelled PO that priced this part, or None. Ordered by order date (ids
    do not follow date order); a line's price is per pack, so divide by
    pack_qty, then add that line's share of the order's delivery cost."""
    row = s.execute(
        select(
            POLine.price,
            SupplierPart.pack_qty,
            PurchaseOrder.id,
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
    if row is None:
        return None
    return (row[0] / row[1] * _delivery_factor(s, row[2]), row[2], po_ref(row[2]))


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
    """What the order this stock came from paid per item, delivery included, or
    None if that order has no price for it. Prefers the booked line (the exact
    line received into this item); migrated stock has no Booking, so fall back
    to that order's line for the same part."""
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
    if booked[0] <= 0:
        return None
    return booked[0] / booked[1] * _delivery_factor(s, po_id)


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
def refresh_all_prices(s: Session, log: bool = False) -> None:
    """Recompute every part's price, then every stock item's. The repair/
    backfill path (e.g. after an import); normal writes keep prices current
    incrementally. `log` writes an Activity row -- off for the startup and
    import runs, which would otherwise log on every boot."""
    # one query up front instead of one per part and per stock item (4k+ on a
    # real dataset); cleared in the finally so a failure cannot leave a stale
    # map behind for the next write
    _factor_cache.update({po: factor for po, (_goods, factor) in po_totals(s).items()})
    try:
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
    finally:
        _factor_cache.clear()
    if log:
        parts = s.scalar(select(func.count()).select_from(Part))
        _activity(s, "refresh_prices", f"Recalculated prices for {parts} parts", [])


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
    component_virtual, component_price, component_price_partial, note) for one
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
                BomLine.note,
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
    for _lid, component_id, _sku, _desc, qty, virtual, price, *_r in bom_for(
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
    label = build_ref(build_id)
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
    changes = _field_changes(part, assembly=assembly)
    part.assembly = assembly
    s.flush()
    refresh_part_price(s, part_id)  # the pricing rule itself changed
    if changes:
        _activity(
            s,
            "set_part_assembly",
            f"Part {part.description}: {', '.join(changes)}",
            [("part", part_id, part.sku or "")],
        )


@_write
def set_part_virtual(s: Session, part_id: int, virtual: bool) -> None:
    part = get_part(s, part_id)
    if virtual and part.assembly:
        raise InventoryError(f"part {part_id} is an assembly; cannot be virtual")
    changes = _field_changes(part, virtual=virtual)
    part.virtual = virtual
    if not virtual:
        part.estimated_price = None  # a hand-entered price no longer applies
    s.flush()
    refresh_part_price(s, part_id)  # the pricing rule itself changed
    if changes:
        _activity(
            s,
            "set_part_virtual",
            f"Part {part.description}: {', '.join(changes)}",
            [("part", part_id, part.sku or "")],
        )


@_write
def set_part_purchasable(s: Session, part_id: int, purchasable: bool) -> None:
    part = get_part(s, part_id)
    if not purchasable and s.scalar(
        select(SupplierPart).where(SupplierPart.part_id == part_id).limit(1)
    ):
        raise InventoryError(
            f"part {part_id} is sold by a supplier; remove those supplier parts first"
        )
    changes = _field_changes(part, purchasable=purchasable)
    part.purchasable = purchasable
    if changes:
        _activity(
            s,
            "set_part_purchasable",
            f"Part {part.description}: {', '.join(changes)}",
            [("part", part_id, part.sku or "")],
        )


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
    changes = _field_changes(part, estimated_price=price)
    part.estimated_price = price
    part.price_partial = price is None
    s.flush()
    refresh_part_price(s, part_id)  # cascade to the assemblies using it
    if changes:
        _activity(
            s,
            "set_part_price",
            f"Part {part.description}: {', '.join(changes)}",
            [("part", part_id, part.sku or "")],
        )


@_write
def add_bomline(
    s: Session,
    line_id: int,
    parent_part_id: int,
    component_part_id: int,
    quantity: float,
    note: str | None = None,
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
            note=note or None,
        )
    )
    s.flush()
    refresh_part_price(s, parent_part_id)
    component = get_part(s, component_part_id)
    _activity(
        s,
        "add_bomline",
        f"BOM {parent.description}: added {component.description} x{quantity:g}",
        [("part", parent.id, parent.sku), ("part", component.id, component.sku)],
    )


@_write
def edit_bomline_quantity(s: Session, line_id: int, quantity: float) -> None:
    if quantity <= 0:
        raise InventoryError("bom quantity must be positive")
    line = get_bomline(s, line_id)
    old = line.quantity
    line.quantity = quantity
    s.flush()
    refresh_part_price(s, line.parent_part_id)
    if quantity != old:
        parent = get_part(s, line.parent_part_id)
        component = get_part(s, line.component_part_id)
        _activity(
            s,
            "edit_bomline",
            f"BOM {parent.description}: {component.description} {old} -> {quantity}",
            [("part", parent.id, parent.sku), ("part", component.id, component.sku)],
        )


@_write
def set_bomline_note(s: Session, line_id: int, note: str | None) -> None:
    line = get_bomline(s, line_id)
    old = line.note
    line.note = note or None
    if line.note != old:
        parent = get_part(s, line.parent_part_id)
        component = get_part(s, line.component_part_id)
        _activity(
            s,
            "set_bomline_note",
            f"BOM {parent.description}: {component.description} note "
            f"{old or '-'} -> {line.note or '-'}",
            [("part", parent.id, parent.sku), ("part", component.id, component.sku)],
        )


@_write
def remove_bomline(s: Session, line_id: int) -> None:
    # ponytail: bom lines are order detail, plain delete (not master data)
    line = get_bomline(s, line_id)
    parent_part_id = line.parent_part_id
    parent = get_part(s, parent_part_id)
    component = get_part(s, line.component_part_id)
    quantity = line.quantity
    s.delete(line)
    s.flush()
    refresh_part_price(s, parent_part_id)
    _activity(
        s,
        "remove_bomline",
        f"BOM {parent.description}: removed {component.description} x{quantity:g}",
        [("part", parent.id, parent.sku), ("part", component.id, component.sku)],
    )


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
    part = get_part(s, part_id)
    _activity(
        s,
        "create_item",
        f"Created a stock item for {part.description}",
        [("stock", item_id, f"0x {part.description}"), ("part", part_id, part.sku)],
    )


@_write
def set_count(s: Session, item_id: int, count: float) -> None:
    item = get_item(s, item_id)
    old = item.count
    # a row's sign is what it is: shelf stock (>= 0) is not a debt and a debt
    # row is not shelf stock. Either may be edited to any value on its own side
    # of zero -- zero is the settled end of both -- but never across, which
    # would silently turn stock owed to a build into stock on hand. A debt is
    # identified by its build stamp, not by its count: settling one to zero
    # must not leave a row that can then be raised positive.
    # (Consumed rows carry the same stamp, hence the status check -- a debt is
    # the Available half of that pair.)
    debt = old < 0 or (
        item.status == STOCK_AVAILABLE and item.consumed_by_build_id is not None
    )
    if count < 0 and not debt:
        raise InventoryError(f"count of item {item_id} cannot be {count}")
    if count > 0 and debt:
        raise InventoryError(
            f"item {item_id} is stock owed to a build; its count cannot be {count}"
        )
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
    item = get_item(s, item_id)
    changes = _field_changes(item, status=status)
    item.status = status
    if changes:
        part = get_part(s, item.part_id)
        _activity(
            s,
            "set_item_status",
            f"Stock of {part.description}: {', '.join(changes)}",
            [
                ("stock", item_id, f"{item.count:g}x {part.description}"),
                ("part", item.part_id, part.sku),
            ],
        )


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
        get_build(s, build_id)  # exists check; the label is derived from the id
        refs.append(("build", build_id, build_ref(build_id)))
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
    get_build(s, build_id)  # exists check
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
    label = build_ref(build_id)
    _activity(
        s,
        "add_negative_stock",
        f"Recorded {quantity:g}x {part.description} used by {label} but never booked",
        [("part", part_id, part.sku), ("build", build_id, label)],
    )


def _debt_rows(s: Session, part_id: int) -> list[StockItem]:
    """Outstanding shortfalls for a part, oldest first: negative Available rows
    an order owes. Same predicate _settle_stock_debt uses on the receipt side."""
    return list(
        s.scalars(
            select(StockItem)
            .where(
                StockItem.part_id == part_id,
                StockItem.status == STOCK_AVAILABLE,
                StockItem.count < 0,
                or_(
                    StockItem.consumed_by_build_id.is_not(None),
                    StockItem.consumed_by_so_id.is_not(None),
                ),
            )
            .order_by(StockItem.id)
        ).all()
    )


def _paired_placeholder(s: Session, debt: StockItem) -> StockItem | None:
    """The consumed row a debt was written with: its immediate predecessor, the
    pairing contract _consume_fifo and add_negative_stock emit. Matching on
    order+part instead would pick up the rows consumed from real stock."""
    placeholder = s.get(StockItem, debt.id - 1)
    if (
        placeholder is None
        or placeholder.status != STOCK_CONSUMED
        or placeholder.consumed_by_build_id != debt.consumed_by_build_id
        or placeholder.consumed_by_so_id != debt.consumed_by_so_id
        or placeholder.part_id != debt.part_id
    ):
        return None
    return placeholder


def _settle_from_shelf(
    s: Session,
    debt: StockItem,
    placeholder: StockItem,
    quantity: float,
    sources: list[StockItem],
) -> None:
    """Pay `quantity` of one debt out of `sources`, oldest first. The debt
    shrinks, the sources are drawn down and the placeholder consumption is
    repriced to what that stock actually cost, so the order stops being costed
    at a guess. Shared by the manual settle and the bulk consolidate."""
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
                consumed_by_build_id=debt.consumed_by_build_id,
                consumed_by_so_id=debt.consumed_by_so_id,
                status=STOCK_CONSUMED,
                unit_price=source.unit_price,
                price_basis=source.price_basis,
                price_po_id=source.price_po_id,
            )
        )
        next_id += 1
    s.flush()
    # the assembly was costed off the estimate; its inputs are real now. A sale
    # produces nothing, so there is nothing to reprice for one.
    if debt.consumed_by_build_id is not None:
        for produced in s.scalars(
            select(StockItem).where(
                StockItem.build_id == debt.consumed_by_build_id,
                StockItem.status == STOCK_AVAILABLE,
            )
        ):
            refresh_stock_price(s, produced)


def _debt_order(s: Session, debt: StockItem) -> tuple[str, int, str]:
    """(kind, id, label) of the order a debt is owed to, checking it exists."""
    if debt.consumed_by_build_id is not None:
        get_build(s, debt.consumed_by_build_id)  # exists check
        return "build", debt.consumed_by_build_id, build_ref(debt.consumed_by_build_id)
    if debt.consumed_by_so_id is None:
        raise InventoryError(f"stock item {debt.id} is not linked to an order")
    get_so(s, debt.consumed_by_so_id)  # exists check
    return "sales-order", debt.consumed_by_so_id, so_ref(debt.consumed_by_so_id)


def _shelf_stock(s: Session, part_id: int) -> list[StockItem]:
    """Available stock of a part that can settle a debt, oldest first."""
    return list(
        s.scalars(
            select(StockItem)
            .where(
                StockItem.part_id == part_id,
                StockItem.status == STOCK_AVAILABLE,
                StockItem.count > 0,  # never settle a debt out of another debt
            )
            .order_by(StockItem.id)
        ).all()
    )


@_write
def settle_debt_from_stock(
    s: Session, debt_id: int, quantity: float, item_id: int | None = None
) -> None:
    """Pay off a build or sales-order shortfall out of stock already on the
    shelf.

    The receipt path (_settle_stock_debt) does the same when parts arrive on a
    PO; this is the manual version for a part that ended up with both a debt row
    and available stock. The debt shrinks, the sources are drawn down FIFO (or
    off one named item) and the placeholder consumption is repriced to what that
    stock actually cost, so the order stops being costed at a guess."""
    debt = get_item(s, debt_id)
    if debt.status != STOCK_AVAILABLE or debt.count >= 0:
        raise InventoryError(f"stock item {debt_id} is not an outstanding debt")
    if quantity <= 0:
        raise InventoryError("quantity must be positive")
    if quantity > -debt.count + 1e-9:
        raise InventoryError(f"stock item {debt_id} only owes {-debt.count:g}")
    kind, order_id, label = _debt_order(s, debt)
    placeholder = _paired_placeholder(s, debt)
    if placeholder is None:
        raise InventoryError(f"stock item {debt_id} has no consumption to settle")
    if item_id is None:
        sources = _shelf_stock(s, debt.part_id)
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

    _settle_from_shelf(s, debt, placeholder, quantity, sources)
    part = get_part(s, debt.part_id)
    _activity(
        s,
        "settle_debt_from_stock",
        f"{quantity:g}x {part.description} settled a shortfall on {label} from stock",
        [
            ("stock", debt_id, f"{debt.count:g}x {part.description}"),
            (kind, order_id, label),
            ("part", part.id, part.sku),
        ],
    )


@_write
def consolidate_part_stock(s: Session, part_id: int) -> None:
    """Clear every outstanding shortfall for a part out of its available stock,
    oldest debt first until the shelf runs out. The bulk version of
    settle_debt_from_stock, for a part that ended up holding both."""
    part = get_part(s, part_id)
    debts = _debt_rows(s, part_id)
    if not debts:
        raise InventoryError(f"part {part_id} has no outstanding shortfall")
    sources = _shelf_stock(s, part_id)
    left = sum(i.count for i in sources)
    if left <= 1e-9:
        raise InventoryError(f"part {part_id} has no available stock to consolidate")

    total = 0.0
    refs: list[tuple[str, int, str | None]] = [("part", part.id, part.sku)]
    for debt in debts:
        if left <= 1e-9:
            break
        placeholder = _paired_placeholder(s, debt)
        if placeholder is None:
            continue  # hand-edited data: no consumption to reprice, leave it
        pay = min(-debt.count, left, placeholder.count)
        if pay <= 1e-9:
            continue
        kind, order_id, label = _debt_order(s, debt)
        _settle_from_shelf(s, debt, placeholder, pay, _shelf_stock(s, part_id))
        total += pay
        left -= pay
        refs.append((kind, order_id, label))
    if total <= 1e-9:
        raise InventoryError(f"part {part_id} has no shortfall that can be settled")
    _activity(
        s,
        "consolidate_part_stock",
        f"{total:g}x {part.description} consolidated from stock, "
        f"settling {len(refs) - 1} shortfall(s)",
        refs,
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


def next_po_id() -> int:
    with session() as s:
        return (s.scalar(select(func.max(PurchaseOrder.id))) or 0) + 1


def next_line_id() -> int:
    with session() as s:
        return (s.scalar(select(func.max(POLine.id))) or 0) + 1


def _validate_code(code: str, what: str) -> None:
    if not code or len(code) > 64 or any(c.isspace() for c in code):
        raise InventoryError(f"invalid {what} {code!r}: 1-64 chars, no whitespace")


def _validate_supplier_sku(
    s: Session, supplier_id: int, sku: str | None, sp_id: int | None = None
) -> None:
    """Reject a malformed sku, or one this supplier already uses. sp_id is the
    supplier part being written, so re-saving its own sku is not a clash."""
    if sku is None:
        return
    if len(sku) > 64 or any(c.isspace() for c in sku):
        raise InventoryError(
            f"invalid supplier part sku {sku!r}: max 64 chars, no whitespace"
        )
    clash = s.scalar(
        select(SupplierPart).where(
            SupplierPart.supplier_id == supplier_id,
            SupplierPart.sku == sku,
            SupplierPart.id != sp_id,
        )
    )
    if clash is not None:
        raise InventoryError(
            f"sku {sku!r} is already used by this supplier (supplier part {clash.id})"
        )


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
    supplier = get_supplier(s, supplier_id)
    changes = _field_changes(supplier, name=name)
    supplier.name = name
    if changes:
        _activity(
            s,
            "edit_supplier",
            f"Supplier: {', '.join(changes)}",
            [("supplier", supplier_id, name)],
        )


@_write
def set_supplier_active(s: Session, supplier_id: int, active: bool) -> None:
    supplier = get_supplier(s, supplier_id)
    if not _field_changes(supplier, active=active):
        return
    supplier.active = active
    _activity(
        s,
        "set_supplier_active",
        f"{'Activated' if active else 'Deactivated'} supplier {supplier.name}",
        [("supplier", supplier_id, supplier.name)],
    )


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
    sku: str | None,
    part_id: int,
    description: str = "",
    ean: str = "",
    hyperlink: str = "",
    pack_qty: int = 1,
) -> None:
    sku = _normalise_sku(sku)
    if s.get(SupplierPart, sp_id) is not None:
        raise InventoryError(f"supplier part {sp_id} already exists")
    get_supplier(s, supplier_id)
    _validate_supplier_sku(s, supplier_id, sku)
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
        f"Created supplier part {sku or part.description} for {part.description}",
        [("supplier-part", sp_id, sku or ""), ("part", part_id, part.sku)],
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
    changes = _field_changes(
        sp,
        part_id=part_id,
        description=description,
        ean=ean,
        hyperlink=hyperlink,
        pack_qty=pack_qty,
    )
    sp.part_id = part_id
    sp.description = description
    sp.ean = ean
    sp.hyperlink = hyperlink
    sp.pack_qty = pack_qty  # divides the line price, so prices move with it
    s.flush()
    refresh_part_price(s, part_id)
    if was_part_id != part_id:
        refresh_part_price(s, was_part_id)
    if changes:
        part = get_part(s, part_id)
        _activity(
            s,
            "edit_supplier_part",
            f"Supplier part {_supplier_part_label(s, sp)}: {', '.join(changes)}",
            [("supplier-part", sp_id, sp.sku or ""), ("part", part_id, part.sku)],
        )


@_write
def set_supplier_part_sku(s: Session, sp_id: int, sku: str | None) -> None:
    sku = _normalise_sku(sku)
    sp = get_supplier_part(s, sp_id)
    _validate_supplier_sku(s, sp.supplier_id, sku, sp_id)
    changes = _field_changes(sp, sku=sku)
    sp.sku = sku
    if changes:
        part = get_part(s, sp.part_id)
        _activity(
            s,
            "set_supplier_part_sku",
            f"Supplier part for {part.description}: {', '.join(changes)}",
            [("supplier-part", sp_id, sku or ""), ("part", sp.part_id, part.sku)],
        )


@_write
def set_supplier_part_active(s: Session, sp_id: int, active: bool) -> None:
    sp = get_supplier_part(s, sp_id)
    if not _field_changes(sp, active=active):
        return
    sp.active = active
    _activity(
        s,
        "set_supplier_part_active",
        f"{'Activated' if active else 'Deactivated'} supplier part "
        f"{_supplier_part_label(s, sp)}",
        [
            ("supplier-part", sp_id, sp.sku or ""),
            ("part", sp.part_id, get_part(s, sp.part_id).sku),
        ],
    )


@_write
def create_po(
    s: Session,
    po_id: int,
    supplier_id: int,
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
    reference = po_ref(po_id)
    s.add(
        PurchaseOrder(
            id=po_id,
            supplier_id=supplier_id,
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
    # receipts already created real stock that cancelling can't undo. Expert
    # mode lifts both -- the escape hatch for an order cancelled by mistake.
    expert = expert_mode()
    if (
        not expert
        and po.status in (POStatus.COMPLETE, POStatus.CANCELLED)
        and status != po.status
    ):
        raise InventoryError(
            f"purchase order {po_id} is {po.status}, cannot change status"
        )
    if not expert and status == POStatus.CANCELLED and _po_has_receipts(s, po_id):
        raise InventoryError(
            f"purchase order {po_id} has received lines, cannot cancel"
        )
    if start_date is None:
        raise InventoryError("purchase order start date is required")
    reprice = (
        status != po.status
        or start_date != po.start_date
        or delivery_cost != po.delivery_cost
    )
    changes = _field_changes(
        po,
        status=status,
        start_date=start_date,
        end_date=end_date,
        delivery_cost=delivery_cost,
        supplier_reference=supplier_reference,
        description=description,
    )
    po.status = status
    po.start_date = start_date
    po.end_date = end_date
    po.delivery_cost = delivery_cost
    po.supplier_reference = supplier_reference
    po.description = description
    if reprice:
        # cancelling drops this order's prices; the date decides which order is
        # "latest"; the delivery cost is spread over the lines -- any of the
        # three reprices every part on the order
        _refresh_po_parts(s, po_id)
    if changes:
        label = po_ref(po_id)
        _activity(
            s,
            "edit_po",
            f"Edited {label}: {', '.join(changes)}",
            [("po", po_id, label)],
        )


def _refresh_po_parts(s: Session, po_id: int) -> None:
    """Reprice every part on one order. Anything that moves an order's goods
    subtotal moves the delivery-cost share of *every* line on it, so a single
    line's price or quantity is never a one-part change."""
    s.flush()  # the write must be visible to the recompute
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
    _refresh_po_parts(s, po_id)
    part = get_part(s, sp.part_id)
    _activity(
        s,
        "add_po_line",
        f"{po_ref(po_id)} line added: {quantity}x "
        f"{sp.description or part.description} at {price:g}",
        [("purchase-order", po_id, po_ref(po_id)), ("part", part.id, part.sku)],
    )


@_write
def edit_po_line(
    s: Session,
    line_id: int,
    quantity: int | None = None,
    price: float | None = None,
) -> None:
    line = get_po_line(s, line_id)
    _check_po_line_editable(s, line)
    changes = []
    if quantity is not None:
        if quantity <= 0:
            raise InventoryError("po line quantity must be positive")
        if quantity < line.received:
            raise InventoryError(
                f"po line {line_id}: quantity {quantity} below {line.received} already received"
            )
        if quantity != line.quantity:
            changes.append(f"quantity {line.quantity} -> {quantity}")
        line.quantity = quantity
    if price is not None:
        if price != line.price:
            changes.append(f"price {line.price} -> {price}")
        if price < 0:
            raise InventoryError("po line price must not be negative")
        line.price = price
    if changes:
        # price feeds latest_po_unit_price, and both price and quantity move the
        # order's goods subtotal, hence every line's share of the delivery cost
        _refresh_po_parts(s, line.po_id)
        # stock booked against this line was valued at the old price
        for item in s.scalars(
            select(StockItem)
            .join(Booking, Booking.stock_item_id == StockItem.id)
            .where(Booking.po_line_id == line_id)
        ):
            refresh_stock_price(s, item)
        part = get_part(s, get_supplier_part(s, line.supplier_part_id).part_id)
        label = po_ref(line.po_id)
        _activity(
            s,
            "edit_po_line",
            f"{label} line {part.description}: {', '.join(changes)}",
            [("po", line.po_id, label), ("part", part.id, part.sku)],
        )


@_write
def remove_po_line(s: Session, line_id: int) -> None:
    # ponytail: PO lines are order detail, plain delete (not master data)
    line = get_po_line(s, line_id)
    _check_po_line_editable(s, line)
    if s.scalar(select(Booking).where(Booking.po_line_id == line_id).limit(1)):
        raise InventoryError(f"po line {line_id} has bookings, cannot remove")
    sp = get_supplier_part(s, line.supplier_part_id)
    part_id = sp.part_id
    po_id = line.po_id
    quantity = line.quantity
    s.delete(line)
    s.flush()  # the line must be gone before "latest price" is recomputed
    refresh_part_price(s, part_id)  # its own part is no longer on the order
    _refresh_po_parts(s, po_id)  # the rest share the delivery cost differently
    part = get_part(s, part_id)
    _activity(
        s,
        "remove_po_line",
        f"{po_ref(po_id)} line removed: {quantity}x "
        f"{sp.description or part.description}",
        [("purchase-order", po_id, po_ref(po_id)), ("part", part_id, part.sku)],
    )


def _check_po_line_editable(s: Session, line: POLine) -> None:
    po = get_po(s, line.po_id)
    if po.status in (POStatus.COMPLETE, POStatus.CANCELLED):
        raise InventoryError(
            f"purchase order {po.id} is {po.status}, cannot edit lines"
        )


def _settle_stock_debt(s: Session, item: StockItem) -> dict[tuple[str, int], float]:
    """Pay off outstanding shortfalls for a part out of freshly received stock.
    A shortfall left a negative Available row plus a placeholder consumed row
    priced at the part's estimate; the parts have now arrived, so the debt
    shrinks toward zero and the consumed row is stamped with this PO and
    repriced to what was actually paid -- the order stops being costed at a
    guess. Settled units never reach the shelf, so `item` is reduced by them.

    Both builds and sales can owe stock, so the result is keyed by which:
    {("build"|"sales-order", id): quantity settled}. Only a build has output to
    reprice afterwards -- a sale produces nothing."""
    debts = _debt_rows(s, item.part_id)
    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    settled: dict[tuple[str, int], float] = {}
    for debt in debts:
        if item.count <= 1e-9:
            break
        placeholder = _paired_placeholder(s, debt)
        if placeholder is None:
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
                consumed_by_so_id=debt.consumed_by_so_id,
                status=STOCK_CONSUMED,
            )
            s.add(placeholder)
            next_id += 1
        placeholder.po_id = item.po_id
        s.flush()
        refresh_stock_price(s, placeholder)
        debt.count += pay
        item.count -= pay
        # exactly one of the two is set: the debt query requires at least one,
        # and a row is consumed by a build or a sale, never both
        # not-null: the debt query requires one of the two to be set
        key: tuple[str, int] = (
            ("build", debt.consumed_by_build_id)
            if debt.consumed_by_build_id is not None
            else ("sales-order", debt.consumed_by_so_id)
        )  # ty: ignore[invalid-assignment]
        settled[key] = settled.get(key, 0.0) + pay
    # the assemblies those builds produced were costed off the estimate; now
    # that their inputs are real, reprice the output. A sale produces nothing,
    # so there is nothing to reprice for it.
    for kind, order_id in settled:
        if kind != "build":
            continue
        for produced in s.scalars(
            select(StockItem).where(
                StockItem.build_id == order_id, StockItem.status == STOCK_AVAILABLE
            )
        ):
            refresh_stock_price(s, produced)
    return settled


def _receive_line(
    s: Session, line: POLine, stock_item_id: int, quantity: float
) -> tuple[StockItem, dict[tuple[str, int], float]]:
    """Receive `quantity` packs of one PO line into a new stock item: create it,
    book it to the line, price it, and pay off any shortfalls for that part out
    of it. Returns (the stock item, {(kind, order_id): quantity settled}).
    Shared by book_po_line and book_po so the receive paths cannot drift."""
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
    s: Session,
    settled: dict[tuple[str, int], float],
    part: Part,
    origin: tuple[str, int, str],
) -> None:
    """One activity row per order the new stock paid off, so receiving (or
    producing) tells the user what it settled instead of silently zeroing rows.
    `origin` is the (kind, id, label) of the order the stock came in on."""
    for (kind, order_id), qty in settled.items():
        if kind == "build":
            get_build(s, order_id)  # exists check
            label = build_ref(order_id)
        else:
            get_so(s, order_id)  # exists check
            label = so_ref(order_id)
        _activity(
            s,
            "settle_stock_debt",
            f"{qty:g}x {part.description} settled a shortfall on {label}",
            [
                origin,
                (kind, order_id, label),
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
    po_label = po_ref(po.id)
    _activity(
        s,
        "book_po_line",
        f"Received {count:g}x {part.description} into stock",
        [
            ("po", po.id, po_label),
            ("stock", stock_item_id, f"{count:g}x {part.description}"),
        ],
    )
    _settle_activity(s, settled, part, ("po", po.id, po_label))
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
    po_label = po_ref(po_id)
    refs = [("po", po_id, po_label)]
    all_settled: list[tuple[dict[tuple[str, int], float], Part]] = []
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
        _settle_activity(s, settled, part, ("po", po_id, po_label))
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
    s.add(
        BuildOrder(
            id=build_id,
            part_id=part_id,
            quantity=quantity,
            status=status,
            description=description,
            start_date=start_date,
            end_date=end_date,
        )
    )
    _snapshot_build_lines(s, build_id, part_id)
    label = build_ref(build_id)
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
    # Draft/Pending/Cancelled). Expert mode lifts both.
    expert = expert_mode()
    if not expert and status == BuildStatus.COMPLETE and already != quantity:
        raise InventoryError(
            f"build {build_id}: cannot complete, produced {already} of {quantity}"
        )
    started_states = (BuildStatus.PRODUCTION, BuildStatus.COMPLETE)
    if not expert and already > 0 and status not in started_states:
        raise InventoryError(
            f"build {build_id}: {already} already produced; status must be "
            "Production or Complete"
        )
    changes = _field_changes(
        build,
        quantity=quantity,
        status=status,
        description=description,
        start_date=start_date,
        end_date=end_date,
    )
    build.quantity = quantity
    build.status = status
    build.description = description
    build.start_date = start_date
    build.end_date = end_date
    if changes:
        label = build_ref(build_id)
        _activity(
            s,
            "edit_build",
            f"Edited {label}: {', '.join(changes)}",
            [("build", build_id, label)],
        )


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


def _consume_fifo(s: Session, part_id: int, need: float, next_id: int, **stamp) -> int:
    """Take `need` units of `part_id` out of stock, oldest first, and return the
    next free stock id. `stamp` is the column naming the consumer -- a build
    (`consumed_by_build_id`) or a sale (`consumed_by_so_id`).

    Consumption never destroys stock: each source Available row is drawn down
    and a matching STOCK_CONSUMED row is split off inheriting its price, so what
    was used and what it cost stay traceable. Running short does not block --
    the shortfall becomes a consumed row at the part's estimate plus a NEGATIVE
    Available debt row, which nets out of on-hand counts until a PO settles it.
    """
    items = s.scalars(
        select(StockItem)
        .where(
            StockItem.part_id == part_id,
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
                part_id=part_id,
                po_id=item.po_id,  # keep the source's price provenance
                # the source's producing build carries over (this split is
                # the same stock); the order eating it is the consumer
                build_id=item.build_id,
                status=STOCK_CONSUMED,
                # inherit the source's resolved price: what this stock was
                # actually worth when it was consumed
                unit_price=item.unit_price,
                price_basis=item.price_basis,
                price_po_id=item.price_po_id,
                **stamp,
            )
        )
        next_id += 1
    # FIFO left need > 0: stock ran short. The order used these parts anyway, so
    # record them consumed at the part's estimate (so the consumer is costed in
    # full, as an estimate rather than a floor) and carry the debt as a negative
    # Available row. When the parts arrive, book_po_line settles the debt and
    # reprices the consumed row to what was actually paid.
    if need > 1e-9:
        price = get_part(s, part_id).estimated_price
        basis = PriceBasis.ESTIMATE if price is not None else PriceBasis.NONE
        # consumed row first, debt row second, always adjacent ids:
        # _settle_stock_debt pairs them by `debt.id - 1`
        for count in (need, -need):
            s.add(
                StockItem(
                    id=next_id,
                    count=count,
                    part_id=part_id,
                    status=STOCK_CONSUMED if count > 0 else STOCK_AVAILABLE,
                    unit_price=price,
                    price_basis=basis,
                    **stamp,
                )
            )
            next_id += 1
    return next_id


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
        next_id = _consume_fifo(
            s, component_id, qty * quantity, next_id, consumed_by_build_id=build_id
        )

    output = StockItem(
        id=next_id,
        count=quantity,
        part_id=build.part_id,
        build_id=build_id,
        status=STOCK_AVAILABLE,
    )
    s.add(output)
    s.flush()
    part = get_part(s, build.part_id)
    label = build_ref(build_id)
    # an earlier order may already owe this assembly. Settle it out of the fresh
    # output, exactly as receiving a PO does -- settled units never reach the
    # shelf, so this must run before the reprice below reads their counts.
    settled = _settle_stock_debt(s, output)
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
    _activity(
        s,
        "produce_build",
        f"{label}: produced {quantity}x {part.description}",
        [
            ("build", build_id, label),
            ("stock", next_id, f"{quantity}x {part.description}"),
        ],
    )
    _settle_activity(s, settled, part, ("build", build_id, label))
    if quantity >= remaining:
        build.status = BuildStatus.COMPLETE


# -- selling ----------------------------------------------------------------


def get_so(s: Session, so_id: int) -> SalesOrder:
    so = s.get(SalesOrder, so_id)
    if so is None:
        raise InventoryError(f"no sales order {so_id}")
    return so


def next_line_part_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(SalesOrderLinePart.id))) or 0) + 1


def so_lines_for(s: Session, so_id: int) -> list[SalesOrderLine]:
    return list(
        s.scalars(
            select(SalesOrderLine)
            .where(SalesOrderLine.so_id == so_id)
            .order_by(SalesOrderLine.id)
        )
    )


def line_parts_for(s: Session, line_id: int) -> list:
    """(link_id, part_id, sku, description, quantity, estimated_price, in_stock)
    for one line item's manual part mapping.

    A part-less "ignore" row comes back with part_id None and the Part columns
    empty -- the caller splits it out; see api.list_sales_order_lines."""
    in_stock = (
        select(
            StockItem.part_id.label("part_id"),
            func.coalesce(func.sum(StockItem.count), 0.0).label("qty"),
        )
        .where(StockItem.status == STOCK_AVAILABLE)
        .group_by(StockItem.part_id)
        .subquery()
    )
    return list(
        s.execute(
            select(
                SalesOrderLinePart.id,
                Part.id,
                Part.sku,
                Part.description,
                SalesOrderLinePart.quantity,
                Part.estimated_price,
                func.coalesce(in_stock.c.qty, 0.0),
            )
            # outer: an ignore row has no part and must still come back
            .outerjoin(Part, SalesOrderLinePart.part_id == Part.id)
            .join(in_stock, in_stock.c.part_id == Part.id, isouter=True)
            .where(SalesOrderLinePart.line_id == line_id)
            .order_by(SalesOrderLinePart.id)
        )
    )


def get_line_part(s: Session, link_id: int) -> SalesOrderLinePart:
    link = s.get(SalesOrderLinePart, link_id)
    if link is None:
        raise InventoryError(f"no sales order line part {link_id}")
    return link


def _get_so_line(s: Session, line_id: int) -> SalesOrderLine:
    line = s.get(SalesOrderLine, line_id)
    if line is None:
        raise InventoryError(f"no sales order line {line_id}")
    return line


def _check_unbooked(s: Session, so: SalesOrder) -> None:
    """Reject changing a link a booking has already acted on.

    Adding a link to a booked order is fine -- booking again consumes the delta
    (see so_outstanding). Changing or removing one is not: those units are
    already out of stock, so honouring it would mean un-consuming them, putting
    stock back on the shelf and unwinding any paired debt row.

    What blocks the edit is the consumption, not the flag: an order booked
    without consuming stock (`consume=False`, or a row marked booked by hand)
    has taken nothing, so its links are still free to change."""
    if so.booked and so_consumed(s, so.id):
        raise InventoryError(
            f"sales order {so.id} is booked and has consumed stock; its existing "
            f"parts can no longer be changed (you can still add parts and book again)"
        )


@_write
def add_line_part(
    s: Session, link_id: int, line_id: int, part_id: int, quantity: float
) -> None:
    """Link a part to a sold line item: what this product consumes, per sold
    unit. Manual by design -- WooCommerce knows the SKU it sold, not the parts
    behind it.

    Adding a part the line already lists **adds to** that link rather than
    being rejected: one line uses one quantity of a given part, and "two more
    of those nuts" is the natural way to correct it. There is one row per
    (line, part), which the unique constraint enforces anyway.

    Allowed on a booked order too. Both cases only ever *raise* what the order
    needs, and booking consumes the difference, so nothing already taken out of
    stock is disturbed -- unlike edit_line_part, which can lower a quantity and
    is therefore frozen once booked."""
    _add_line_part(s, link_id, _get_so_line(s, line_id), part_id, quantity)


@_write
def add_extra_part(s: Session, link_id: int, so_id: int, part_id: int, quantity: float):
    """Link a part to the order itself rather than to anything it sold -- the
    cable or handful of bolts thrown in with the shipment.

    It hangs off a synthetic zero-priced line item (models.EXTRAS_WC_LINE_ID),
    created here on first use, so it is an ordinary SalesOrderLinePart: demand,
    booking, costing and the margin pick it up with no code of their own."""
    so = get_so(s, so_id)
    line = s.scalar(
        select(SalesOrderLine).where(
            SalesOrderLine.so_id == so.id,
            SalesOrderLine.wc_line_id == EXTRAS_WC_LINE_ID,
        )
    )
    if line is None:
        line = SalesOrderLine(
            id=(s.scalar(select(func.max(SalesOrderLine.id))) or 0) + 1,
            so_id=so.id,
            wc_line_id=EXTRAS_WC_LINE_ID,
            description=EXTRAS_DESCRIPTION,
            quantity=1,
        )
        s.add(line)
        s.flush()
    _add_line_part(s, link_id, line, part_id, quantity)


def _add_line_part(
    s: Session, link_id: int, line: SalesOrderLine, part_id: int, quantity: float
) -> None:
    if quantity <= 0:
        raise InventoryError("quantity must be positive")
    line_id = line.id
    so = get_so(s, line.so_id)
    part = get_part(s, part_id)
    if s.scalar(
        select(SalesOrderLinePart).where(
            SalesOrderLinePart.line_id == line_id,
            SalesOrderLinePart.part_id.is_(None),
        )
    ):
        raise InventoryError(
            "this line is ignored; remove that before linking parts to it"
        )
    label = so_ref(so.id)
    existing = s.scalar(
        select(SalesOrderLinePart).where(
            SalesOrderLinePart.line_id == line_id,
            SalesOrderLinePart.part_id == part_id,
        )
    )
    if existing is not None:
        was = existing.quantity
        existing.quantity = was + quantity
        _activity(
            s,
            "add_line_part",
            f"{label}: {line.description or line.sku} now uses "
            f"{existing.quantity:g}x {part.description} (was {was:g})",
            [("sales-order", so.id, label), ("part", part_id, part.sku)],
        )
        return
    s.add(
        SalesOrderLinePart(
            id=link_id, line_id=line_id, part_id=part_id, quantity=quantity
        )
    )
    _activity(
        s,
        "add_line_part",
        f"{label}: {line.description or line.sku} uses {quantity:g}x {part.description}",
        [("sales-order", so.id, label), ("part", part_id, part.sku)],
    )


@_write
def ignore_line(s: Session, link_id: int, line_id: int) -> None:
    """Mark a sold line item as consuming nothing -- shipping, a fee, a service,
    anything that leaves no part off the shelf.

    Written as a real (part-less) link row rather than as the absence of one:
    the blank then reads as a decision the user made, so the line counts as
    linked and stops being outstanding work. Rejected once the line has parts --
    "ignore this" and "it uses these" cannot both be true.

    Allowed on a booked order, like add_line_part and unlike edit/remove: the
    marker consumes nothing, so there is no stock movement to unwind. A line
    left unlinked when the order was booked is exactly where this is needed --
    booking does not make it any more linked than it was."""
    line = _get_so_line(s, line_id)
    so = get_so(s, line.so_id)
    existing = s.scalars(
        select(SalesOrderLinePart).where(SalesOrderLinePart.line_id == line_id)
    ).all()
    if any(r.part_id is None for r in existing):
        return  # already ignored; a no-op should not manufacture an activity row
    if existing:
        raise InventoryError(
            "this line already uses parts; remove them before ignoring it"
        )
    s.add(SalesOrderLinePart(id=link_id, line_id=line_id, part_id=None, quantity=0))
    label = so_ref(so.id)
    _activity(
        s,
        "ignore_line",
        f"{label}: {line.description or line.sku} is ignored (consumes nothing)",
        [("sales-order", so.id, label)],
    )


@_write
def edit_line_part(s: Session, link_id: int, quantity: float) -> None:
    if quantity <= 0:
        raise InventoryError("quantity must be positive")
    link = get_line_part(s, link_id)
    so = get_so(s, _get_so_line(s, link.line_id).so_id)
    _check_unbooked(s, so)
    if quantity == link.quantity:
        return  # a no-op patch should not manufacture an activity row
    if link.part_id is None:
        raise InventoryError("an ignored line has no quantity to change")
    part = get_part(s, link.part_id)
    label = so_ref(so.id)
    _activity(
        s,
        "edit_line_part",
        f"{label}: {part.description} quantity {link.quantity:g} -> {quantity:g}",
        [("sales-order", so.id, label), ("part", part.id, part.sku)],
    )
    link.quantity = quantity


@_write
def remove_line_part(s: Session, link_id: int) -> None:
    # ponytail: the mapping is order detail, plain delete (not master data)
    link = get_line_part(s, link_id)
    line = _get_so_line(s, link.line_id)
    so = get_so(s, line.so_id)
    _check_unbooked(s, so)
    label = so_ref(so.id)
    refs = [("sales-order", so.id, label)]
    if link.part_id is None:
        what = f"{line.description or line.sku} is no longer ignored"
    else:
        part = get_part(s, link.part_id)
        what = f"no longer uses {part.description}"
        refs.append(("part", part.id, part.sku))
    s.delete(link)
    _activity(s, "remove_line_part", f"{label}: {what}", refs)


# -- part skus -------------------------------------------------------------
#
# The map from what WooCommerce sells to what it is made of. A sales line
# carries a sku; a sku names one part, and that part's own BOM says what it is
# made of. So a sold product built from several components is an assembly, and
# two skus that are the same build (a door-left and a door-right variant) both
# point at it -- the recipe is written once.
#
# Before schema 14 a sku carried its own (part, quantity) list here, a second
# BOM mechanism beside the real one that could not express that sharing.


def part_skus(s: Session) -> list:
    """(id, sku, part_id, part_sku, part_description, part_assembly) for every
    mapping row."""
    return list(
        s.execute(
            select(
                PartSku.id,
                PartSku.sku,
                Part.id,
                Part.sku,
                Part.description,
                Part.assembly,
            )
            # outer: an ignore row has no part and must still be listed
            .outerjoin(Part, PartSku.part_id == Part.id)
            .order_by(PartSku.sku)
        )
    )


def next_part_sku_id() -> int:
    # ponytail: max+1 id generation; fine while one process owns the db
    with session() as s:
        return (s.scalar(select(func.max(PartSku.id))) or 0) + 1


def sold_skus(s: Session) -> list[tuple[str, str, int, bool]]:
    """(sku, an example description, how many lines carry it, already mapped)
    for every non-blank sku the imported sales orders actually use.

    What the mapping page offers instead of a free-text box: these skus are
    WooCommerce's, so typing one by hand is a chance to typo a key that then
    silently matches nothing. The description is whichever line's it happens to
    be -- the same sku sells under a tidied-up name sometimes, and any of them
    identifies it well enough to pick from a list."""
    mapped = set(s.scalars(select(PartSku.sku)))
    rows = s.execute(
        select(
            SalesOrderLine.sku,
            func.min(SalesOrderLine.description),
            func.count(),
        )
        .where(SalesOrderLine.sku != "")
        .group_by(SalesOrderLine.sku)
        .order_by(func.count().desc(), SalesOrderLine.sku)
    )
    return [(sku, desc, n, sku in mapped) for sku, desc, n in rows]


def get_part_sku(s: Session, link_id: int) -> PartSku:
    row = s.get(PartSku, link_id)
    if row is None:
        raise InventoryError(f"no part sku {link_id}")
    return row


def _claim_sku(s: Session, sku: str) -> str:
    """Reject a blank sku, or one another part already answers to."""
    sku = sku.strip()
    if not sku:
        raise InventoryError("sku is required")
    taken = s.scalar(select(PartSku).where(PartSku.sku == sku))
    if taken is not None:
        if taken.part_id is None:
            raise InventoryError(f"sku {sku} is set to be ignored; remove that first")
        part = get_part(s, taken.part_id)
        raise InventoryError(f"sku {sku} is already sold as {part.description}")
    return sku


@_write
def add_part_sku(s: Session, link_id: int, part_id: int, sku: str) -> None:
    """Sell a part under one more sales sku.

    A part may answer to several (variants of the same build); a sku may name
    only one part, so claiming one already taken is an error rather than a
    silent move -- the user should see which part has it."""
    part = get_part(s, part_id)
    sku = _claim_sku(s, sku)
    s.add(PartSku(id=link_id, part_id=part_id, sku=sku))
    _activity(
        s,
        "add_part_sku",
        f"{part.description} is sold as {sku}",
        [("part", part_id, part.sku)],
    )


@_write
def ignore_sku(s: Session, link_id: int, sku: str) -> None:
    """Mark a sold sku as consuming nothing, so every order carrying it prefills
    as ignored instead of waiting to be linked by hand.

    The remembered form of ignore_line: shipping lines, fees and services come
    back on every order, and deciding to ignore one should only happen once."""
    sku = sku.strip()
    if not sku:
        raise InventoryError("sku is required")
    taken = s.scalar(select(PartSku).where(PartSku.sku == sku))
    if taken is not None:
        if taken.part_id is None:
            return  # already ignored; a no-op should not manufacture a row
        part = get_part(s, taken.part_id)
        raise InventoryError(
            f"sku {sku} is sold as {part.description}; unlink that before ignoring it"
        )
    s.add(PartSku(id=link_id, part_id=None, sku=sku))
    _activity(s, "ignore_sku", f"Sku {sku} is ignored (consumes nothing)", [])


@_write
def remove_part_sku(s: Session, link_id: int) -> None:
    # ponytail: a prefill lookup table, plain delete (orders keep their lines)
    row = get_part_sku(s, link_id)
    refs = []
    if row.part_id is None:
        what = f"Sku {row.sku} is no longer ignored"
    else:
        part = get_part(s, row.part_id)
        what = f"{part.description} is no longer sold as {row.sku}"
        refs.append(("part", part.id, part.sku))
    s.delete(row)
    _activity(s, "remove_part_sku", what, refs)


@_write
def prefill_so_parts(s: Session, so_id: int) -> None:
    """The manual "prefill from SKUs" write, logging what it filled in. The
    importer calls _prefill_so_parts directly instead: it prefills every order
    it touches and logs one row for the whole run.

    Returns nothing, like every other @_write (the decorator commits and
    re-exports around the call, and drops what fn returns); the route re-reads
    the lines to show what changed."""
    filled, lines = _prefill_so_parts(s, so_id)
    if filled:
        label = so_ref(so_id)
        _activity(
            s,
            "prefill_so_parts",
            f"{label}: filled in {filled} part link(s) on {lines} line(s) from "
            f"the product sku mappings",
            [("sales-order", so_id, label)],
        )


@_write
def prefill_all_so_parts(s: Session, result: dict) -> None:
    """The list page's "prefill from SKUs" over every order at once. One write,
    one activity row, one commit -- the per-order route in a client-side loop
    would be N of each. Counts go out through result, since @_write drops what
    fn returns (the importer reports its own run the same way)."""
    for (so_id,) in s.execute(select(SalesOrder.id).order_by(SalesOrder.id)):
        got, _ = _prefill_so_parts(s, so_id)
        if got:
            result["filled"] += got
            result["orders"] += 1
    if result["filled"]:
        _activity(
            s,
            "prefill_all_so_parts",
            f"Filled in {result['filled']} part link(s) on {result['orders']} "
            f"sales order(s) from the product sku mappings",
            [],
        )


def _prefill_so_parts(s: Session, so_id: int) -> tuple[int, int]:
    """Fill in the parts of every line whose sku names a part. Returns (links
    written, lines filled); the caller logs.

    One sold unit consumes one of that part, so the link is written with
    quantity 1 -- a sold product made of more than one thing is an assembly, and
    the line consumes one of it off the shelf (built by a build order), which is
    what selling a built product does. A part-less mapping row is the "ignore"
    marker and prefills a line that consumes nothing.

    Only lines that have no parts yet are touched: once the user has edited a
    line, the mapping is a starting point they already moved on from. A booked
    order is fair game for the same reason add_line_part is allowed on one --
    booking no longer implies every line is linked (an order can be booked
    without consuming stock), and the links it adds are booked as the delta."""
    so = get_so(s, so_id)
    link_id = (s.scalar(select(func.max(SalesOrderLinePart.id))) or 0) + 1
    filled = 0
    lines = 0
    for line in so_lines_for(s, so.id):
        if not line.sku:
            continue
        if s.scalar(
            select(func.count())
            .select_from(SalesOrderLinePart)
            .where(SalesOrderLinePart.line_id == line.id)
        ):
            continue
        mapping = s.scalar(select(PartSku).where(PartSku.sku == line.sku))
        if mapping is None:
            continue  # a sku nobody has mapped yet
        s.add(
            SalesOrderLinePart(
                id=link_id,
                line_id=line.id,
                part_id=mapping.part_id,  # None = the ignore marker
                quantity=1.0,
            )
        )
        link_id += 1
        filled += 1
        lines += 1
    return filled, lines


def so_needs(s: Session, so_id: int) -> dict[int, float]:
    """{part_id: units} one sales order consumes, summed over its line items --
    each line's sold quantity times the per-unit quantity of each linked part.

    Ignored lines carry a part-less marker row and consume nothing, so they are
    excluded here -- everything downstream (demand, booking, costing) reads this."""
    needs: dict[int, float] = {}
    for part_id, units in s.execute(
        select(
            SalesOrderLinePart.part_id,
            func.sum(SalesOrderLinePart.quantity * SalesOrderLine.quantity),
        )
        .join(SalesOrderLine, SalesOrderLinePart.line_id == SalesOrderLine.id)
        .where(SalesOrderLine.so_id == so_id, SalesOrderLinePart.part_id.is_not(None))
        .group_by(SalesOrderLinePart.part_id)
    ):
        needs[part_id] = units
    return needs


def so_consumed(s: Session, so_id: int) -> dict[int, float]:
    """{part_id: units} this order has already taken out of stock.

    Consumed rows only: the paired debt row is negative Available, and counting
    it would cancel out the very shortfall it records."""
    return {
        part_id: units
        for part_id, units in s.execute(
            select(StockItem.part_id, func.sum(StockItem.count))
            .where(
                StockItem.consumed_by_so_id == so_id,
                StockItem.status == STOCK_CONSUMED,
            )
            .group_by(StockItem.part_id)
        )
    }


def so_outstanding(s: Session, so_id: int) -> dict[int, float]:
    """{part_id: units} mapped but not yet consumed -- what booking would take.

    Booking is the delta, not the whole mapping, so a part linked after the
    order was booked is picked up by booking again without re-consuming what
    already went out."""
    consumed = so_consumed(s, so_id)
    out = {}
    for part_id, need in so_needs(s, so_id).items():
        remaining = need - consumed.get(part_id, 0.0)
        if remaining > 1e-9:
            out[part_id] = remaining
    return out


def so_shortages(s: Session, so_id: int) -> list[tuple[int, str, float, float]]:
    """(part_id, description, needed, in_stock) for every part this order would
    run short of. Read-only: booking goes ahead anyway (the shortfall becomes a
    debt), so this is what the produce-style confirm dialog warns with."""
    out = []
    for part_id, need in sorted(so_outstanding(s, so_id).items()):
        if get_part(s, part_id).virtual:
            continue  # unlimited: labour is never short
        have = (
            s.scalar(
                select(func.coalesce(func.sum(StockItem.count), 0.0)).where(
                    StockItem.part_id == part_id,
                    StockItem.status == STOCK_AVAILABLE,
                )
            )
            or 0.0
        )
        if need > have + 1e-9:
            out.append((part_id, get_part(s, part_id).description, need, have))
    return out


@_write
def set_so_actual_shipping(s: Session, so_id: int, cost: float | None) -> None:
    """What shipping really cost us. None means unknown, which is not zero: the
    margin then leaves shipping out altogether (see api._so_out)."""
    so = get_so(s, so_id)
    old = so.actual_shipping_cost
    so.actual_shipping_cost = cost
    if cost != old:
        label = so_ref(so_id)
        _activity(
            s,
            "set_so_actual_shipping",
            f"{label} actual shipping {old if old is not None else '-'} -> "
            f"{cost if cost is not None else '-'}",
            [("sales-order", so_id, label)],
        )


@_write
def book_sales_order(s: Session, so_id: int, consume: bool = True) -> None:
    """Consume the parts a sale used, FIFO, in one transaction.

    The build analogue is produce_build, and consumption is literally the same
    code (_consume_fifo) -- with two differences: the rows are stamped
    consumed_by_so_id, and nothing is produced. A sale ships stock out; there is
    no output to cost. Shortages do not block: the missing quantity becomes a
    debt that a later PO receipt settles.

    consume=False books the order without touching stock -- for historical
    orders whose stock was already taken off the shelf outside Stronghold."""
    so = get_so(s, so_id)
    # what is mapped but not yet taken. Booking an already-booked order is how
    # parts linked afterwards get consumed, so this is the delta, not the whole
    # mapping -- and an order with nothing outstanding is not an error.
    needs = so_outstanding(s, so_id) if consume else {}
    if so.booked and not needs and consume:
        raise InventoryError(f"sales order {so_id} has nothing left to book")
    next_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
    for part_id, need in sorted(needs.items()):
        part = get_part(s, part_id)
        if part.virtual:
            # unlimited stock, so nothing is drawn down -- but the sale did use
            # it (labour), and that cost is real. Same shape produce_build uses:
            # a consumed row at the part's rate, which keeps it out of the stock
            # value report (Available rows only) while counting in so_cost.
            price = part.estimated_price
            s.add(
                StockItem(
                    id=next_id,
                    count=need,
                    part_id=part_id,
                    consumed_by_so_id=so_id,
                    status=STOCK_CONSUMED,
                    unit_price=price,
                    price_basis=PriceBasis.VIRTUAL
                    if price is not None
                    else PriceBasis.NONE,
                )
            )
            next_id += 1
            continue
        next_id = _consume_fifo(s, part_id, need, next_id, consumed_by_so_id=so_id)
    was_booked = so.booked
    so.booked = True
    label = so_ref(so_id)
    if not consume:
        message = f"Booked {label} without consuming stock"
    elif not needs:
        # a sale that consumes nothing is still a sale: booking records that it
        # has been dealt with (services, digital goods, stock handled elsewhere)
        message = f"Booked {label}: no parts to consume"
    elif was_booked:
        message = f"Booked {label}: consumed {len(needs)} further part(s) from stock"
    else:
        message = f"Booked {label}: consumed {len(needs)} part(s) from stock"
    _activity(s, "book_sales_order", message, [("sales-order", so_id, label)])


def so_revenue(s: Session, so_id: int) -> float:
    """What the sale brought in, ex VAT and excluding shipping -- shipping is a
    pass-through cost, not margin on the goods."""
    return (
        s.scalar(
            select(
                func.coalesce(
                    func.sum(SalesOrderLine.unit_price * SalesOrderLine.quantity), 0.0
                )
            ).where(SalesOrderLine.so_id == so_id)
        )
        or 0.0
    )


def so_cost(s: Session, so_id: int) -> tuple[float | None, float | None]:
    """(estimated, realised) cost of the parts a sale consumes.

    Estimated comes from the linked parts' current estimates and is available
    before booking -- it is what the margin column shows on an open order.
    Realised is what the consumed rows actually cost and exists only once
    booked. None on either side means nothing priced it."""
    estimated: float | None = None
    for part_id, need in so_needs(s, so_id).items():
        price = get_part(s, part_id).estimated_price
        if price is not None:
            estimated = (estimated or 0.0) + price * need

    realised: float | None = None
    if get_so(s, so_id).booked:
        for price, count in s.execute(
            select(StockItem.unit_price, StockItem.count).where(
                StockItem.consumed_by_so_id == so_id,
                StockItem.status == STOCK_CONSUMED,
            )
        ):
            if price is not None:
                realised = (realised or 0.0) + price * count
    return estimated, realised


# -- demand -----------------------------------------------------------------


def part_demand(s: Session) -> dict[int, tuple[float, float, float, float]]:
    """Per part: (units still needed by planned builds, units needed by unbooked
    sales, units still to be received on open POs, units still to be produced by
    open builds of this part). Four grouped queries, not one per part.

    The last one is the assembly mirror of incoming: an open build order is
    stock on its way in, just made rather than bought.

    A build counts once it is Pending -- planned is what you buy against; Draft
    is the scratchpad status and asks for nothing. Its need is its snapshot's
    per-unit quantity times the units it has left to produce; a PO line's is the
    packs still outstanding times pack_qty. A sale counts while it is unbooked
    and not cancelled/refunded/failed: booking is what consumes the stock, so a
    booked order has already taken its parts and asks for nothing more. Virtual
    components hold no stock, so they are never needed."""
    for_builds: dict[int, float] = {}
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
            for_builds[part_id] = for_builds.get(part_id, 0.0) + per_unit * remaining

    for_sales: dict[int, float] = {}
    for part_id, units in s.execute(
        select(
            SalesOrderLinePart.part_id,
            func.sum(SalesOrderLinePart.quantity * SalesOrderLine.quantity),
        )
        .join(SalesOrderLine, SalesOrderLinePart.line_id == SalesOrderLine.id)
        .join(SalesOrder, SalesOrderLine.so_id == SalesOrder.id)
        .join(Part, SalesOrderLinePart.part_id == Part.id)
        .where(
            SalesOrder.booked.is_(False),
            SalesOrder.status.not_in(no_demand_statuses()),
            Part.virtual.is_(False),
        )
        .group_by(SalesOrderLinePart.part_id)
    ):
        for_sales[part_id] = units

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

    in_production: dict[int, float] = {}
    for build_id, part_id, qty in s.execute(
        select(BuildOrder.id, BuildOrder.part_id, BuildOrder.quantity).where(
            BuildOrder.status.in_((BuildStatus.PENDING, BuildStatus.PRODUCTION))
        )
    ):
        remaining = qty - produced_qty(s, build_id)
        if remaining > 0:
            in_production[part_id] = in_production.get(part_id, 0.0) + remaining

    return {
        pid: (
            for_builds.get(pid, 0.0),
            for_sales.get(pid, 0.0),
            incoming.get(pid, 0.0),
            in_production.get(pid, 0.0),
        )
        for pid in set(for_builds) | set(for_sales) | set(incoming) | set(in_production)
    }


def stock_shortages(s: Session) -> list[tuple[int, float, float]]:
    """(part_id, in_stock, needed) for every active non-assembly part, short or
    not: a full stock position against what the open sales orders ask for.
    Negative means short, and the caller sorts those to the top.

    A pure sales-vs-stock simulation: build orders are ignored entirely. An
    assembly that comes out short is exploded through its BOM as if it were
    built -- only the shortfall, since the units already on the shelf cover
    that many sales -- and its components carry the demand down, recursively.
    Assemblies therefore never appear in the result, only the parts you can
    actually buy or make time for."""
    need = {pid: sales for pid, (_, sales, _, _) in part_demand(s).items() if sales}
    stock = {
        part_id: total
        for part_id, total in s.execute(
            select(StockItem.part_id, func.sum(StockItem.count))
            .where(StockItem.status == STOCK_AVAILABLE)
            .group_by(StockItem.part_id)
        )
    }
    bom: dict[int, list[tuple[int, float]]] = {}
    for parent_id, component_id, qty in s.execute(
        select(BomLine.parent_part_id, BomLine.component_part_id, BomLine.quantity)
    ):
        bom.setdefault(parent_id, []).append((component_id, qty))
    assemblies = set(s.scalars(select(Part.id).where(Part.assembly.is_(True))))

    # An assembly's shortfall depends on its total need, and several parents may
    # each add to it, so it must not explode until every parent above it has.
    # Hence a topological order (parents before children) rather than a plain
    # recursion, which would settle a shared sub-assembly on first sight and
    # silently drop the demand from its second parent.
    parents: dict[int, list[int]] = {}
    for parent_id, lines in bom.items():
        for component_id, _ in lines:
            parents.setdefault(component_id, []).append(parent_id)

    order: list[int] = []
    state: dict[int, int] = {}  # 1 = on the current path, 2 = emitted

    def visit(part_id: int) -> None:
        if state.get(part_id):
            return  # already emitted, or a BOM cycle: stop rather than loop
        state[part_id] = 1
        for parent_id in parents.get(part_id, ()):
            visit(parent_id)
        state[part_id] = 2
        order.append(part_id)

    # every active part is reported, so every active part is walked -- plus the
    # inactive ones still sitting in a BOM or holding stock, which must explode
    # (or be counted) even though they are not listed themselves
    active = set(s.scalars(select(Part.id).where(Part.active.is_(True))))
    for part_id in sorted(active | set(need) | set(stock) | set(bom) | set(parents)):
        visit(part_id)

    out = []
    for part_id in order:
        short = need.get(part_id, 0.0) - stock.get(part_id, 0.0)
        if part_id in assemblies:
            if short > 1e-9:  # only the shortfall is built; the shelf covers the rest
                for component_id, per_unit in bom.get(part_id, ()):
                    need[component_id] = need.get(component_id, 0.0) + short * per_unit
        elif part_id in active:
            out.append((part_id, stock.get(part_id, 0.0), need.get(part_id, 0.0)))
    return out


def inactive_part_suspects(s: Session) -> list[tuple[int, int]]:
    """(part_id, inactive_parents) for every active part that looks like it
    could be deactivated: it is used in no BOM at all, or every assembly it is
    used in is itself inactive -- and in neither case does a sold sku or a sales
    order line map to it.

    inactive_parents comes back so the user can see which of the two cases a row
    is (0 = used nowhere). This is a suggestion, not a verdict: nothing is
    deactivated here, and stock on hand is deliberately not considered -- a part
    with stock left is exactly the one worth deciding about."""
    used_in: dict[int, list[int]] = {}  # component -> [active parents, inactive]
    for component_id, parent_active in s.execute(
        select(BomLine.component_part_id, Part.active).join(
            Part, BomLine.parent_part_id == Part.id
        )
    ):
        counts = used_in.setdefault(component_id, [0, 0])
        counts[0 if parent_active else 1] += 1
    sold = set(
        s.scalars(select(PartSku.part_id).where(PartSku.part_id.is_not(None)))
    ) | set(
        s.scalars(
            select(SalesOrderLinePart.part_id).where(
                SalesOrderLinePart.part_id.is_not(None)
            )
        )
    )
    return [
        (part_id, used_in.get(part_id, (0, 0))[1])
        for part_id in s.scalars(
            select(Part.id).where(Part.active.is_(True)).order_by(Part.id)
        )
        if not used_in.get(part_id, (0, 0))[0] and part_id not in sold
    ]
