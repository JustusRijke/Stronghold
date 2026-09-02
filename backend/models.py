"""All SQLAlchemy models: the inventory domain plus the settings table.

Nothing is hard-deleted. Parts, suppliers and supplier-parts carry an
`active` flag (deactivated, never removed). Stock items, purchase orders and
build orders instead carry a `status` that subsumes active/inactive: stock is
available or consumed; POs/builds use their lifecycle status. PO lines, BOM
lines and sales-order lines are order detail, plainly removable.
"""

from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EnumCode(TypeDecorator):
    """Store a StrEnum as a small int, hand it back as the enum member.

    The data file is the source of truth and is read by humans, but a status
    column holding prose is a column whose *stored* value changes whenever the
    wording does. Codes are the storage encoding only: Python still sees the
    enum, the JSON API still speaks the enum's string, and the numbers live in
    one place -- the `_CODES` map below each enum.

    The mapping is append-only. Renumbering an existing member silently
    reinterprets every row already written, and a `git checkout` of an older
    data file would not notice; add a new number instead.
    """

    impl = Integer
    cache_ok = True

    # POs and builds default to "" (no status yet), which is not an enum member.
    # It gets a code of its own rather than being stored as NULL: these columns
    # are NOT NULL, and the export omits NULL columns, so a NULL would come back
    # as the *schema* default on restore instead of the empty state.
    UNSET = 0

    def __init__(self, enum_cls, codes: Mapping[int, StrEnum]) -> None:
        super().__init__()
        self._enum = enum_cls
        self._to_py = codes
        self._to_db = {v: k for k, v in codes.items()}
        if self.UNSET in codes:
            raise ValueError(f"{enum_cls.__name__}: {self.UNSET} is reserved for unset")
        # a missing member would fail to store at all
        missing = set(enum_cls) - set(codes.values())
        if missing:
            raise ValueError(f"{enum_cls.__name__} members without a code: {missing}")

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return self.UNSET
        return self._to_db[self._enum(value)]

    def process_result_value(self, value, dialect):
        if value is None or value == self.UNSET:
            return ""
        return self._to_py[value]


# An order's human code is its pk in the zero-padded shape the InvenTree import
# produced (PO-0042). Derived, never stored: it was always a copy of the pk, so
# storing it only allowed it to be edited into something that lied.
PO_PREFIX = "PO-"
BUILD_PREFIX = "BO-"
SO_PREFIX = "SO-"


def po_ref(po_id: int) -> str:
    return f"{PO_PREFIX}{po_id:04d}"


def build_ref(build_id: int) -> str:
    return f"{BUILD_PREFIX}{build_id:04d}"


def so_ref(so_id: int) -> str:
    return f"{SO_PREFIX}{so_id:04d}"


class PriceBasis(StrEnum):
    """How a stock item's cached unit_price was arrived at."""

    PO = "po"  # the order this stock came from priced it
    BUILD = "build"  # produced by a build order, costed from what it consumed
    BUILD_PARTIAL = "build_partial"  # as BUILD, but some consumed stock was itself
    # estimated or a component ran short: a floor, not the exact cost
    ESTIMATE = "estimate"  # the part's estimated price (latest PO, or BOM roll-up)
    VIRTUAL = "virtual"  # a virtual component (labour) consumed by a build, at
    # the rate its build snapshotted. Exact, not a guess: the rate is hand-set,
    # so it does not make the build's cost partial.
    PO_NO_PRICE = "po_no_price"  # from an order, but nothing prices this part
    NONE = "none"  # no order, no price


# stored codes; append-only, never renumber (see EnumCode)
PRICE_BASIS_CODES = {
    1: PriceBasis.PO,
    2: PriceBasis.BUILD,
    3: PriceBasis.BUILD_PARTIAL,
    4: PriceBasis.ESTIMATE,
    5: PriceBasis.VIRTUAL,
    6: PriceBasis.PO_NO_PRICE,
    7: PriceBasis.NONE,
}


class BuildStatus(StrEnum):
    """The values a build's status may take. Mostly InvenTree BuildStatus
    labels, with two deliberate departures: Draft (ours, the status a new build
    starts in -- not yet planned, so it asks for no stock) and no On Hold, which
    the import folds into Pending. StrEnum members compare/serialise as their
    string value."""

    DRAFT = "Draft"
    PENDING = "Pending"
    PRODUCTION = "Production"
    CANCELLED = "Cancelled"
    COMPLETE = "Complete"


BUILD_STATUS_CODES = {
    1: BuildStatus.DRAFT,
    2: BuildStatus.PENDING,
    3: BuildStatus.PRODUCTION,
    4: BuildStatus.CANCELLED,
    5: BuildStatus.COMPLETE,
}


class POStatus(StrEnum):
    """InvenTree PurchaseOrderStatus labels; the only values a PO's status may take."""

    PENDING = "Pending"
    PLACED = "Placed"
    ON_HOLD = "On Hold"
    COMPLETE = "Complete"
    CANCELLED = "Cancelled"
    LOST = "Lost"
    RETURNED = "Returned"


PO_STATUS_CODES = {
    1: POStatus.PENDING,
    2: POStatus.PLACED,
    3: POStatus.ON_HOLD,
    4: POStatus.COMPLETE,
    5: POStatus.CANCELLED,
    6: POStatus.LOST,
    7: POStatus.RETURNED,
}


class SalesOrderStatus(StrEnum):
    """WooCommerce's own order statuses. Imported, never set in Stronghold --
    WooCommerce owns the commercial lifecycle of a sale."""

    PENDING = "pending"
    PROCESSING = "processing"
    ON_HOLD = "on-hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED = "failed"


SALES_ORDER_STATUS_CODES = {
    1: SalesOrderStatus.PENDING,
    2: SalesOrderStatus.PROCESSING,
    3: SalesOrderStatus.ON_HOLD,
    4: SalesOrderStatus.COMPLETED,
    5: SalesOrderStatus.CANCELLED,
    6: SalesOrderStatus.REFUNDED,
    7: SalesOrderStatus.FAILED,
}

# statuses that ask for nothing: a cancelled or failed sale needs no stock, so
# it raises no demand (see db.part_demand)
SO_DEAD_STATUSES = (
    SalesOrderStatus.CANCELLED,
    SalesOrderStatus.REFUNDED,
    SalesOrderStatus.FAILED,
)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    """A domain setting whose value differs from its default (db.DOMAIN_DEFAULTS)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class Part(Base):
    """The master catalog record every stock item references. The id is a local
    max+1 primary key; sku is an optional human code, unique when set (a blank
    is stored as NULL, and SQLite treats every NULL as distinct, so any number
    of parts may go without one -- see db._normalise_sku).
    assembly marks a part built from other parts (its BomLine rows list the
    components). virtual marks a part with unlimited stock (e.g. labour): it
    never has a stock item and is only used for BOM price calculation; builds
    never consume it. assembly and virtual are mutually exclusive. InvenTree pks
    are only a transient mapping during the one-shot migration (see
    migrate_inventree.py), never stored here."""

    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    assembly: Mapped[bool] = mapped_column(default=False)
    virtual: Mapped[bool] = mapped_column(default=False)
    # purchasable marks a part that may be bought: only these can have supplier
    # parts (and so appear on purchase orders).
    # server_default so restoring a pre-purchasable inventory.sql (whose INSERTs
    # do not name the column) still loads: existing parts default to purchasable
    purchasable: Mapped[bool] = mapped_column(default=True, server_default="1")
    # A named index rather than unique=True on the column: SQLite renders that
    # as an inline table constraint, which ALTER TABLE cannot drop. db.init has
    # to take the index down before replaying an older file (whose rows may
    # still hold the duplicate skus that migration 5 clears), so it
    # must be droppable -- see db._SKU_INDEXES.
    __table_args__ = (Index("ix_parts_sku_unique", "sku", unique=True),)

    # cached unit price, recomputed on the writes that can change it (see
    # db.refresh_part_price): the latest PO price for a bought part, the BOM
    # roll-up for an assembly. None means nothing prices it yet.
    # price_partial marks an assembly priced from an incomplete BOM (some
    # component has no price), so the number is a floor, not the full cost.
    estimated_price: Mapped[float | None] = mapped_column(default=None)
    price_partial: Mapped[bool] = mapped_column(default=False)


class BomLine(Base):
    """One component line of an assembly part's bill of materials: the parent
    assembly needs `quantity` of the component part. Order detail like POLine
    (no active flag), plainly removable; a component appears once per parent."""

    __tablename__ = "bom_lines"
    __table_args__ = (UniqueConstraint("parent_part_id", "component_part_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    component_part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    quantity: Mapped[float]
    # free text from the assembly's designer ("fit last", "M3x8 only"). Optional
    # and never interpreted -- it rides along to the BOM table and nowhere else.
    note: Mapped[str | None] = mapped_column(default=None)


class StockStatus(StrEnum):
    """Whether a stock row is on the shelf or has been consumed by an order.
    The member values are the wire format (JSON, and the frontend's picklists);
    the *stored* value is the int in STOCK_STATUS_CODES. The label the user
    sees lives in the frontend (see status.ts)."""

    AVAILABLE = "available"
    CONSUMED = "consumed"


STOCK_STATUS_CODES = {
    1: StockStatus.AVAILABLE,
    2: StockStatus.CONSUMED,
}

# the enum is the source of truth; these aliases keep the existing call sites
STOCK_AVAILABLE = StockStatus.AVAILABLE
STOCK_CONSUMED = StockStatus.CONSUMED


class StockItem(Base):
    """An inventory item as (id, count); the referenced part supplies sku
    and description. count is float: parts may be measured (kg, m), not just
    counted. po_id/build_id stamp where the stock came from (received against a
    PO, or produced by a build order). status is STOCK_AVAILABLE or
    STOCK_CONSUMED -- stock never disappears; consuming it FIFO splits a new
    STOCK_CONSUMED row off the source so provenance and the price it was bought
    at stay traceable.

    build_id is always the build that PRODUCED the stock; consumed_by_build_id
    the build that ate it. They are separate fields because one row can be both
    -- assembly output produced by one build and later consumed by another --
    and collapsing them loses the producing link, which is what counts a
    build's output (see db.produced_qty)."""

    __tablename__ = "stock_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    count: Mapped[float] = mapped_column(default=0.0)
    part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    build_id: Mapped[int | None] = mapped_column(ForeignKey("build_orders.id"))
    consumed_by_build_id: Mapped[int | None] = mapped_column(
        ForeignKey("build_orders.id"), default=None
    )
    # the sale that consumed this stock. Separate from consumed_by_build_id:
    # stock is eaten by a build or sold, never both, but collapsing them would
    # lose which kind of order it went to (and a sale produces nothing).
    consumed_by_so_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), default=None
    )
    status: Mapped[str] = mapped_column(
        EnumCode(StockStatus, STOCK_STATUS_CODES), default=STOCK_AVAILABLE
    )
    # cached unit price of THIS stock, which is not the part's price: stock is
    # worth what its own order paid, and only falls back to the part estimate
    # when its order does not price it. price_basis records which (see
    # db.refresh_stock_price / PriceBasis).
    unit_price: Mapped[float | None] = mapped_column(default=None)
    price_basis: Mapped[str] = mapped_column(
        EnumCode(PriceBasis, PRICE_BASIS_CODES), default=PriceBasis.NONE
    )
    price_po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
    # when this row was written. Naive local time, like Activity.at. Set on
    # every row the app writes; NULL only where nothing knows the answer -- a
    # pre-v7 row with no order to date it from (db._to_v7). Nullable precisely
    # so that gap stays visible: stamping such a row with the migration's own
    # "now" would invent a creation date and the data would carry it forever.
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now)
    # set when a stocktake created this row (a correction, not a PO or build).
    # Naive local time, like Activity.at.
    stocktake_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    stocktake_reason: Mapped[str | None] = mapped_column(default=None)


class Supplier(Base):
    """id mirrors the InvenTree company pk during migration; a plain local
    max+1 id otherwise. name is the human label."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)


class SupplierPart(Base):
    """A supplier's product for one of our parts. id mirrors the InvenTree
    supplierpart pk during migration; sku is the supplier's own code, optional
    and unique *per supplier* -- two suppliers may well use the same code for
    the same thing, but one supplier using it twice is a mistake. A blank sku
    is stored as NULL, and NULLs do not collide, so one supplier may have any
    number of parts with no code (see db._normalise_sku)."""

    __tablename__ = "supplier_parts"

    # Unique per supplier, and droppable for the same reason Part's is: an
    # older file still holds the duplicate skus migration 5 clears.
    __table_args__ = (
        Index("ix_supplier_parts_sku_unique", "supplier_id", "sku", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey(Supplier.id))
    sku: Mapped[str | None] = mapped_column(String(64))
    part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    description: Mapped[str] = mapped_column(default="")
    ean: Mapped[str] = mapped_column(default="")
    hyperlink: Mapped[str] = mapped_column(default="")
    pack_qty: Mapped[int] = mapped_column(default=1)
    active: Mapped[bool] = mapped_column(default=True)


class PurchaseOrder(Base):
    """id mirrors the InvenTree order pk during migration. The human PO code
    (e.g. PO-0042) is derived from the id by `reference`, not stored; status is
    the InvenTree status label."""

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey(Supplier.id))
    status: Mapped[str] = mapped_column(EnumCode(POStatus, PO_STATUS_CODES), default="")
    # the order date: NOT NULL because it is the sort key for "latest price" --
    # a dateless order cannot be ranked. Defaulted to today in db.create_po
    start_date: Mapped[date]
    end_date: Mapped[date | None] = mapped_column(default=None)  # target arrival
    delivery_cost: Mapped[float] = mapped_column(default=0.0)
    supplier_reference: Mapped[str] = mapped_column(default="")
    description: Mapped[str] = mapped_column(default="")


class POLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey(PurchaseOrder.id))
    supplier_part_id: Mapped[int] = mapped_column(ForeignKey(SupplierPart.id))
    quantity: Mapped[int]
    received: Mapped[float] = mapped_column(default=0.0)  # qty received into stock
    price: Mapped[float]  # ponytail: float money; switch to int cents if rounding bites


class BuildOrder(Base):
    """The assembly analogue of a PurchaseOrder: an order to build `quantity` of
    an assembly Part. Completing it consumes component stock per the part's live
    BOM (BomLine) and produces one stock item of the assembly (stamped build_id).
    id mirrors the InvenTree build pk during migration. The human build code is
    derived from the id by `reference`, not stored; status is the InvenTree
    BuildStatus label."""

    __tablename__ = "build_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))  # the assembly
    quantity: Mapped[int]  # whole assemblies only; no partial builds
    # units produced before this build was imported. Zero for builds raised in
    # the app (their output is real stock rows); set by the InvenTree migration,
    # which cannot reconstruct output InvenTree has since deleted.
    imported_produced: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(
        EnumCode(BuildStatus, BUILD_STATUS_CODES), default=""
    )
    description: Mapped[str] = mapped_column(default="")
    start_date: Mapped[date | None] = mapped_column(default=None)
    end_date: Mapped[date | None] = mapped_column(default=None)  # target completion


class BuildLine(Base):
    """The BOM of an assembly as it stood when the build order was created: what
    this build intends to consume, per produced unit. Snapshot, not a live view
    -- editing the part's BomLine afterwards must not silently rewrite history,
    which is what prices an old build. Active builds can be resynced on demand
    (db.resync_build_lines); completed/cancelled ones never change."""

    __tablename__ = "build_lines"
    __table_args__ = (UniqueConstraint("build_id", "component_part_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("build_orders.id"))
    component_part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    quantity: Mapped[float]  # per produced unit, like BomLine
    # what the component was estimated at when the build was created. Only
    # virtual components use it (they are never consumed, so nothing else
    # records what the build paid for labour); physical components are costed
    # from the stock actually consumed. None on legacy/imported rows, which
    # fall back to the part's current price.
    unit_price: Mapped[float | None] = mapped_column(default=None)


class SalesOrder(Base):
    """A WooCommerce order, imported read-only: WooCommerce owns the commercial
    facts (customer, status, prices) and Stronghold owns only what it cannot
    know -- which parts a sold product consumes (SalesOrderLinePart).

    The id is a local max+1, NOT the WooCommerce id: unlike the one-shot
    InvenTree migration this import runs repeatedly into a non-empty database,
    so WooCommerce ids would collide with ours. wc_order_id keeps the link and
    is the key re-import matches on. The human code (SO-0042) is derived from
    the id by models.so_ref, not stored."""

    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    wc_order_id: Mapped[int] = mapped_column(unique=True)
    wc_number: Mapped[str] = mapped_column(default="")  # WooCommerce's own code
    customer_name: Mapped[str] = mapped_column(default="")
    shipping_country: Mapped[str] = mapped_column(default="")
    shipping_cost: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(
        EnumCode(SalesOrderStatus, SALES_ORDER_STATUS_CODES), default=""
    )
    date_created: Mapped[date | None] = mapped_column(default=None)
    # whether this order has consumed its parts from stock. One-way: booking is
    # a stock movement, so it is never silently undone by a re-import.
    booked: Mapped[bool] = mapped_column(default=False)


class SalesOrderLine(Base):
    """One WooCommerce line item. Owned by WooCommerce, so there is no edit
    path: a re-import replaces these. sku is the product's code as plain text
    and may be empty -- it is never matched against Part.sku (the part link is
    manual, see SalesOrderLinePart)."""

    __tablename__ = "sales_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    so_id: Mapped[int] = mapped_column(ForeignKey(SalesOrder.id))
    wc_line_id: Mapped[int]  # what re-import preserves part links by
    sku: Mapped[str] = mapped_column(default="")
    description: Mapped[str] = mapped_column(default="")
    unit_price: Mapped[float] = mapped_column(default=0.0)  # ex VAT, as imported
    quantity: Mapped[float] = mapped_column(default=0.0)


class SalesOrderLinePart(Base):
    """Which part, and how many of it, one sold line item consumes. The only
    sales table the user writes to: WooCommerce cannot know what a product is
    made of, so the mapping is entered by hand. Quantity is per sold unit, like
    BomLine."""

    __tablename__ = "sales_order_line_parts"
    __table_args__ = (UniqueConstraint("line_id", "part_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey(SalesOrderLine.id))
    part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    quantity: Mapped[float]


class ProductSkuPart(Base):
    """One part a sold sku consumes, and how many per unit sold.

    The mapping side of SalesOrderLinePart, and deliberately the same shape: a
    sku maps to a *list* of (part, quantity), so one sold product may be made of
    several parts without inventing an assembly to hold them. Prefill copies
    these rows onto a sales line verbatim -- what the mapping says is exactly
    what the line gets, with no expansion step in between.

    That includes an assembly: map a sku to one and the line consumes one of
    that assembly, drawn from the stock a build order produced, which is what
    selling a built product actually does.

    Many skus may share a part (a door-left and a door-right variant are the
    same build), and a sku may name several parts, so the key is the pair.

    It only prefills: copying it onto a sales line writes ordinary
    SalesOrderLinePart rows, which the user is then free to edit. Changing a
    mapping never rewrites an order already filled in.
    """

    __tablename__ = "product_sku_parts"
    __table_args__ = (UniqueConstraint("sku", "part_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str]
    part_id: Mapped[int] = mapped_column(ForeignKey(Part.id))
    quantity: Mapped[float]


class Booking(Base):
    """Links a stock item created by booking a PO line back to that line, so
    pricing and provenance stay queryable."""

    __tablename__ = "bookings"

    stock_item_id: Mapped[int] = mapped_column(
        ForeignKey(StockItem.id), primary_key=True
    )
    po_line_id: Mapped[int] = mapped_column(ForeignKey(POLine.id))


class Activity(Base):
    """A read-only record of a state-changing user action: a plain-text message
    plus refs (JSON list of {type, id, label}) the frontend turns into links.
    Append-only, so id is a plain autoincrement event stream (not the max+1
    scheme used by InvenTree-migrated records). Not an undo log -- just history."""

    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    action: Mapped[
        str
    ]  # ponytail: plain str (write fn name); StrEnum if a picklist is wanted
    message: Mapped[str]  # displayed text, no markup: "Produced 3x Foobar"
    # ponytail: refs as JSON text; normalize to a child table only if filtering by a linked entity is needed
    refs: Mapped[str] = mapped_column(default="[]")
