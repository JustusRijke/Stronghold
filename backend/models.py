"""All SQLAlchemy models: the inventory domain plus the settings table.

Nothing is hard-deleted. Parts, suppliers and supplier-parts carry an
`active` flag (deactivated, never removed). Stock items, purchase orders and
build orders instead carry a `status` string that subsumes active/inactive:
stock is "Available" or "Consumed by build order"; POs/builds use their
lifecycle status. PO lines are order detail, plainly removable.
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# An order's human code is its pk in the zero-padded shape the InvenTree import
# produced (PO-0042). Derived, never stored: it was always a copy of the pk, so
# storing it only allowed it to be edited into something that lied.
PO_PREFIX = "PO-"
BUILD_PREFIX = "BO-"


def po_ref(po_id: int) -> str:
    return f"{PO_PREFIX}{po_id:04d}"


def build_ref(build_id: int) -> str:
    return f"{BUILD_PREFIX}{build_id:04d}"


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


class POStatus(StrEnum):
    """InvenTree PurchaseOrderStatus labels; the only values a PO's status may take."""

    PENDING = "Pending"
    PLACED = "Placed"
    ON_HOLD = "On Hold"
    COMPLETE = "Complete"
    CANCELLED = "Cancelled"
    LOST = "Lost"
    RETURNED = "Returned"


class Base(DeclarativeBase):
    pass


class Setting(Base):
    """A domain setting whose value differs from its default (db.DOMAIN_DEFAULTS)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class Part(Base):
    """The master catalog record every stock item references. The id is a local
    max+1 primary key; sku is a plain human code that need not be unique.
    assembly marks a part built from other parts (its BomLine rows list the
    components). virtual marks a part with unlimited stock (e.g. labour): it
    never has a stock item and is only used for BOM price calculation; builds
    never consume it. assembly and virtual are mutually exclusive. InvenTree pks
    are only a transient mapping during the one-shot migration (see
    migrate_inventree.py), never stored here."""

    __tablename__ = "parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64))
    description: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    assembly: Mapped[bool] = mapped_column(default=False)
    virtual: Mapped[bool] = mapped_column(default=False)
    # purchasable marks a part that may be bought: only these can have supplier
    # parts (and so appear on purchase orders).
    # server_default so restoring a pre-purchasable inventory.sql (whose INSERTs
    # do not name the column) still loads: existing parts default to purchasable
    purchasable: Mapped[bool] = mapped_column(default=True, server_default="1")
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


class StockStatus(StrEnum):
    """Whether a stock row is on the shelf or has been consumed by an order.
    The stored values are short codes, not display text: the label the user
    sees lives in the frontend (see status.ts), so rewording it never means
    migrating data. Schema 3 rewrote the prose values these replaced."""

    AVAILABLE = "available"
    CONSUMED = "consumed"


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
    status: Mapped[str] = mapped_column(default=STOCK_AVAILABLE)
    # cached unit price of THIS stock, which is not the part's price: stock is
    # worth what its own order paid, and only falls back to the part estimate
    # when its order does not price it. price_basis records which (see
    # db.refresh_stock_price / PriceBasis).
    unit_price: Mapped[float | None] = mapped_column(default=None)
    price_basis: Mapped[str] = mapped_column(default=PriceBasis.NONE)
    price_po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"))
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
    supplierpart pk during migration; sku is the supplier's own code (a plain
    human code like Part.sku -- not unique; InvenTree data has placeholder skus
    like "N/A" repeated per supplier)."""

    __tablename__ = "supplier_parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey(Supplier.id))
    sku: Mapped[str] = mapped_column(String(64))
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
    status: Mapped[str] = mapped_column(default="")
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
    status: Mapped[str] = mapped_column(default="")
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
