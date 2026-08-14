"""The JSON API: thin FastAPI routes over db.py. Every route wraps a db
function; validation failures (InventoryError) become HTTP 400. This module owns
no domain logic -- it only shapes requests/responses and maps errors.

The frontend (SvelteKit) is the only consumer; its typed client is generated
from this app's OpenAPI schema, so a renamed field breaks the build, not prod.
"""

import json
from datetime import date, datetime
from typing import Literal

import db
from db import InventoryError
from fastapi import APIRouter, HTTPException
from models import (
    STOCK_AVAILABLE,
    STOCK_CONSUMED,
    Activity,
    BomLine,
    Booking,
    BuildLine,
    BuildOrder,
    BuildStatus,
    Part,
    POLine,
    POStatus,
    PurchaseOrder,
    StockItem,
    Supplier,
    SupplierPart,
)
from pydantic import BaseModel, Field
from settings import Settings
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

router = APIRouter(prefix="/api")

# set by main.create_app; None when uvicorn imports this module directly
settings: Settings | None = None


def _guard(fn, *args, **kwargs):
    """Run a db write, turning a domain rejection into a 400."""
    try:
        return fn(*args, **kwargs)
    except InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# -- schemas ----------------------------------------------------------------
# Response models are flat DTOs; joins (sku/description on stock, part_sku on
# supplier parts) are resolved server-side so the frontend never joins.

# Status picklists live as StrEnums in models.py (single source of truth, also
# used by db.py). ponytail: the frontend validator generator keys enum picklists
# by field name (last write wins), so PO, build and stock "status" collapse into
# one generated STATUS_OPTIONS -- stock's wins. The PO and build picklists are
# hardcoded in frontend/src/lib/status.ts instead of importing the generated one.


class PartOut(BaseModel):
    id: int
    sku: str
    description: str
    active: bool
    assembly: bool
    virtual: bool
    purchasable: bool
    estimated_price: float | None  # cached unit price; None when nothing prices it
    price_partial: bool  # an assembly whose BOM has unpriced components
    in_stock: float  # Available stock on hand (debt rows net out)
    owed: float  # what builds are owed, negative (0 if none); a stocktake's floor
    needed: float  # units builds in production still have to consume
    incoming: float  # units still to be received on open POs
    suggested_order: float  # max(0, needed - in_stock - incoming)


class PartIn(BaseModel):
    sku: str
    description: str = ""
    virtual: bool = False


class PartPatch(BaseModel):
    sku: str | None = None
    description: str | None = None
    active: bool | None = None
    assembly: bool | None = None
    virtual: bool | None = None
    purchasable: bool | None = None
    # virtual parts only: their price is entered by hand. Explicit null clears
    # it, so "absent" and "clear" must stay distinguishable -- hence the
    # model_fields_set check in patch_part rather than a None default alone.
    estimated_price: float | None = Field(default=None, ge=0)


class BomLineOut(BaseModel):
    id: int
    component_part_id: int
    component_sku: str
    component_description: str
    component_virtual: bool
    quantity: float
    unit_price: float | None  # the component's estimated price, None if unpriced
    line_price: float | None  # unit_price * quantity: this line's share of the build
    price_partial: bool  # the component is an assembly with an incomplete BOM


class BomLineIn(BaseModel):
    component_part_id: int
    quantity: float


class BomUsageOut(BaseModel):
    """One assembly that uses this part as a component (the reverse of a BOM)."""

    line_id: int
    parent_part_id: int
    parent_sku: str
    parent_description: str
    quantity: float


StockStatus = Literal["Available", "Consumed by build order"]
PriceBasisOut = Literal[
    "po", "build", "build_partial", "estimate", "virtual", "po_no_price", "none"
]


class StockItemOut(BaseModel):
    id: int
    part_id: int
    sku: str
    description: str
    count: float
    po_id: int | None
    po_reference: str  # "" when po_id is null
    build_id: int | None  # the build that produced this stock
    consumed_by_build_id: int | None  # the build that consumed it
    build_reference: str  # label of build_id, else consumed_by_build_id; "" if neither
    consumed_by_reference: str  # label of consumed_by_build_id; "" when it is null
    status: StockStatus
    unit_price: float | None  # what THIS stock is worth per item
    price_basis: PriceBasisOut
    stocktake_at: datetime | None  # set when a stocktake wrote this row
    stocktake_reason: str  # "" unless a stocktake wrote this row


StockEventKind = Literal[
    "received", "produced", "consumed", "stocktake", "debt", "unknown"
]


class StockLogEntryOut(BaseModel):
    """One thing that happened to a part's stock, derived from the stock row it
    left behind. Rows are the log: consuming stock splits a row rather than
    destroying it, so every event survives as its own row."""

    # null for imported production: InvenTree deleted the stock, so the event is
    # reconstructed from the build's own count and has no row to link to
    item_id: int | None
    kind: StockEventKind
    at: datetime | None  # exact only for stocktakes; else the order's date
    at_approx: bool  # True when `at` came from a PO/build date, not the event
    # signed like a ledger (in positive, out negative). For an incoming event
    # this is what the row holds NOW, not what arrived: consuming stock draws
    # the source row down in place, so a receipt later eaten by a build reads
    # as its remainder. `drawn_down` flags exactly that case.
    quantity: float
    drawn_down: bool  # an incoming row since reduced by later events
    reason: str  # stocktake reason, else ""
    order_label: str  # "PO-0104" / "BO-2913", else ""
    order_url: str  # link to that order, else ""
    unit_price: float | None
    price_basis: PriceBasisOut


class StockItemIn(BaseModel):
    part_id: int


class StocktakeIn(BaseModel):
    part_id: int
    # may be negative only down to what the part already owes (db.stocktake
    # enforces the floor); creating a debt is NegativeStockIn's job
    count: float
    reason: str = Field(min_length=1)
    item_id: int | None = None  # to take from when lowering; oldest if null
    build_id: int | None = None
    po_id: int | None = None


class NegativeStockIn(BaseModel):
    part_id: int
    quantity: float = Field(gt=0)
    build_id: int
    reason: str = ""


class SettleDebtIn(BaseModel):
    quantity: float = Field(gt=0)
    item_id: int | None = None  # stock to settle from; oldest first if null


class StockItemPatch(BaseModel):
    count: float | None = None
    status: StockStatus | None = None


class SupplierOut(BaseModel):
    id: int
    name: str
    active: bool


class SupplierIn(BaseModel):
    name: str = ""


class SupplierPatch(BaseModel):
    name: str | None = None
    active: bool | None = None


class SupplierPartOut(BaseModel):
    id: int
    supplier_id: int
    sku: str
    part_id: int
    part_sku: str
    part_description: str
    description: str
    ean: str
    hyperlink: str
    pack_qty: int
    active: bool
    last_price: float | None = None  # per item (line price / pack_qty)
    last_po_date: str | None = None


class SupplierPartIn(BaseModel):
    supplier_id: int
    sku: str
    part_id: int
    description: str = ""
    ean: str = ""
    hyperlink: str = ""
    pack_qty: int = Field(default=1, gt=0)


class SupplierPartPatch(BaseModel):
    part_id: int | None = None
    description: str | None = None
    ean: str | None = None
    hyperlink: str | None = None
    pack_qty: int | None = Field(default=None, gt=0)
    active: bool | None = None


class PurchaseOrderOut(BaseModel):
    id: int
    supplier_id: int
    reference: str
    status: str
    start_date: date
    end_date: date | None
    delivery_cost: float
    supplier_reference: str
    description: str


class PartPurchaseOrderOut(PurchaseOrderOut):
    quantity: float  # items of this part ordered (packs * pack_qty)
    unit_price: float | None = None


class PurchaseOrderIn(BaseModel):
    supplier_id: int
    reference: str = ""
    status: POStatus = POStatus.PENDING
    # the order date: defaults to today rather than being required of the caller
    start_date: date = Field(default_factory=date.today)
    end_date: date | None = None
    delivery_cost: float = 0.0
    supplier_reference: str = ""
    description: str = Field(min_length=1)


class PurchaseOrderPatch(BaseModel):
    reference: str | None = None
    status: POStatus | None = None
    start_date: date | None = None
    end_date: date | None = None
    delivery_cost: float | None = None
    supplier_reference: str | None = None
    description: str | None = None


class POLineOut(BaseModel):
    id: int
    po_id: int
    supplier_part_id: int
    supplier_sku: str
    pack_qty: int
    quantity: int
    received: float  # ordered units received into stock so far
    price: float


class POLineIn(BaseModel):
    supplier_part_id: int
    quantity: int = Field(gt=0)
    price: float = Field(default=0.0, ge=0)


class BookIn(BaseModel):
    quantity: float = Field(gt=0)


class BuildOrderOut(BaseModel):
    id: int
    part_id: int
    quantity: int
    reference: str
    status: str
    description: str
    start_date: date | None
    end_date: date | None
    produced: int  # whole assemblies already produced into stock
    bom_drifted: bool  # the part's BOM no longer matches this build's snapshot


class PartBuildOut(BuildOrderOut):
    """A build that consumes this part, with what it requires and has taken of it."""

    required: float  # per-unit snapshot quantity * build quantity
    consumed: float  # stock actually consumed so far


class BuildLineOut(BaseModel):
    """One component of a build's snapshot, with what the build has consumed of
    it so far -- a shortfall is what makes the produced cost a floor."""

    id: int
    component_part_id: int
    component_sku: str
    component_description: str
    component_virtual: bool
    quantity: float  # per produced unit
    required: float  # quantity * build quantity
    consumed: float
    unit_price: float | None
    line_price: float | None
    price_partial: bool


class BuildOrderIn(BaseModel):
    part_id: int
    quantity: int = Field(gt=0)
    reference: str = ""
    status: BuildStatus = BuildStatus.DRAFT
    description: str = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None


class BuildOrderPatch(BaseModel):
    quantity: int | None = Field(default=None, gt=0)
    reference: str | None = None
    status: BuildStatus | None = None
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProduceIn(BaseModel):
    quantity: int = Field(gt=0)


class SettingOut(BaseModel):
    key: str
    value: str


class SettingIn(BaseModel):
    value: str


class OkOut(BaseModel):
    ok: bool = True


class ActivityRef(BaseModel):
    type: str  # entity kind the frontend maps to a route: part/stock/po/build/supplier/supplier-part
    id: int
    label: str


class ActivityOut(BaseModel):
    id: int
    at: datetime
    action: str
    message: str
    refs: list[ActivityRef]


# -- parts ------------------------------------------------------------------


def _on_hand(s) -> dict[int, float]:
    return {
        part_id: total
        for part_id, total in s.execute(
            select(StockItem.part_id, func.sum(StockItem.count))
            .where(StockItem.status == STOCK_AVAILABLE)
            .group_by(StockItem.part_id)
        )
    }


def _owed(s) -> dict[int, float]:
    """Per part, what builds are owed (negative). See db.owed."""
    return {
        part_id: total
        for part_id, total in s.execute(
            select(StockItem.part_id, func.sum(StockItem.count))
            .where(StockItem.status == STOCK_AVAILABLE, StockItem.count < 0)
            .group_by(StockItem.part_id)
        )
    }


def _part_out(
    p: Part,
    on_hand: dict[int, float],
    demand: dict[int, tuple[float, float]],
    owed: dict[int, float],
) -> PartOut:
    stock = on_hand.get(p.id, 0.0)
    needed, incoming = demand.get(p.id, (0.0, 0.0))
    return PartOut(
        id=p.id,
        sku=p.sku,
        description=p.description,
        active=p.active,
        assembly=p.assembly,
        virtual=p.virtual,
        purchasable=p.purchasable,
        estimated_price=p.estimated_price,
        price_partial=p.price_partial,
        in_stock=stock,
        owed=owed.get(p.id, 0.0),
        needed=needed,
        incoming=incoming,
        suggested_order=max(0.0, needed - stock - incoming),
    )


@router.get("/parts", response_model=list[PartOut])
def list_parts() -> list[PartOut]:
    with db.session() as s:
        on_hand, demand, owed = _on_hand(s), db.part_demand(s), _owed(s)
        return [
            _part_out(p, on_hand, demand, owed)
            for p in s.scalars(select(Part).order_by(Part.id))
        ]


@router.get("/parts/{part_id}", response_model=PartOut)
def get_part(part_id: int) -> PartOut:
    with db.session() as s:
        part = s.get(Part, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail=f"no part {part_id}")
        return _part_out(part, _on_hand(s), db.part_demand(s), _owed(s))


@router.post("/parts", response_model=PartOut, status_code=201)
def create_part(body: PartIn) -> PartOut:
    new_id = db.next_part_id()
    _guard(db.create_part, new_id, body.sku, body.description, body.virtual)
    return get_part(new_id)


@router.patch("/parts/{part_id}", response_model=PartOut)
def patch_part(part_id: int, body: PartPatch) -> PartOut:
    if body.sku is not None:
        _guard(db.set_part_sku, part_id, body.sku)
    if body.description is not None:
        _guard(db.edit_part, part_id, body.description)
    if body.assembly is not None:
        _guard(db.set_part_assembly, part_id, body.assembly)
    if body.virtual is not None:
        _guard(db.set_part_virtual, part_id, body.virtual)
    if body.purchasable is not None:
        _guard(db.set_part_purchasable, part_id, body.purchasable)
    if "estimated_price" in body.model_fields_set:  # explicit null clears it
        _guard(db.set_part_price, part_id, body.estimated_price)
    if body.active is not None:
        _guard(db.set_part_active, part_id, body.active)
    return get_part(part_id)


def _pos_with_qty(where) -> list[PartPurchaseOrderOut]:
    """POs whose lines match `where`, newest first, with the units ordered and
    the price paid per unit on each."""
    with db.session() as s:
        rows: dict[int, PartPurchaseOrderOut] = {}
        for po, line, pack_qty in s.execute(
            select(PurchaseOrder, POLine, SupplierPart.pack_qty)
            .join(POLine, POLine.po_id == PurchaseOrder.id)
            .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
            .where(where)
            .order_by(PurchaseOrder.start_date.desc(), PurchaseOrder.id.desc())
        ):
            # a part can sit on several lines of one PO: sum the units, keep the last price
            row = rows.setdefault(
                po.id, PartPurchaseOrderOut(**_po_out(po).model_dump(), quantity=0.0)
            )
            row.quantity += line.quantity * pack_qty
            row.unit_price = line.price / pack_qty if line.price else row.unit_price
        return list(rows.values())


@router.get(
    "/parts/{part_id}/purchase-orders", response_model=list[PartPurchaseOrderOut]
)
def list_part_pos(part_id: int) -> list[PartPurchaseOrderOut]:
    """POs that ordered this part, with what this part cost on each."""
    return _pos_with_qty(SupplierPart.part_id == part_id)


@router.get("/parts/{part_id}/used-in", response_model=list[BomUsageOut])
def list_part_used_in(part_id: int) -> list[BomUsageOut]:
    """Assemblies whose BOM lists this part as a component."""
    with db.session() as s:
        return [
            BomUsageOut(
                line_id=line.id,
                parent_part_id=parent.id,
                parent_sku=parent.sku,
                parent_description=parent.description,
                quantity=line.quantity,
            )
            for line, parent in s.execute(
                select(BomLine, Part)
                .join(Part, BomLine.parent_part_id == Part.id)
                .where(BomLine.component_part_id == part_id)
                .order_by(Part.id)
            )
        ]


@router.get("/parts/{part_id}/builds", response_model=list[BuildOrderOut])
def list_part_builds(part_id: int) -> list[BuildOrderOut]:
    """Build orders that build this part (as an assembly)."""
    with db.session() as s:
        return [
            _build_out(s, b)
            for b in s.scalars(
                select(BuildOrder)
                .where(BuildOrder.part_id == part_id)
                .order_by(BuildOrder.id)
            )
        ]


def _stock_log_entries(
    row, booked: dict[int, float], split_of: set[int]
) -> list[StockLogEntryOut]:
    """The events one stock row records -- usually one, sometimes two.

    A consumed row carries both the PO it was bought on and the build that ate
    it, so precedence matters: the event is what happened *to* the stock, and
    its date follows the same order. Dating a consumption by its PO would show
    when the stock was bought, not when it was used.

    Such a row is two events when nothing else records the receipt: the stock
    arrived on that PO and was later consumed. It is one event when the row was
    split off a surviving parent (produce_build copies po_id onto the consumed
    slice for pricing) -- the parent already reports the receipt, so reporting
    it again would double-count what arrived."""
    i, po_ref, po_date, prod_ref, prod_date, eat_ref, eat_date = row
    label, url, at, approx = "", "", None, True
    # a receipt's booking records what actually arrived, so a smaller count now
    # means later events ate into this row
    arrived = booked.get(i.id)
    drawn_down = arrived is not None and i.count < arrived - 1e-9
    if i.consumed_by_build_id is not None:
        # a build link wins over stocktake_at: add_negative_stock stamps both,
        # and what matters there is that a build owes the stock, not that the
        # row was typed in by hand
        kind = "debt" if i.count < 0 else "consumed"
        at = eat_date
        label = eat_ref or f"BO-{i.consumed_by_build_id}"
        url = f"/build-orders/{i.consumed_by_build_id}"
        quantity = i.count if i.count < 0 else -i.count
    elif i.stocktake_at is not None:
        kind = "stocktake"
        at, approx = i.stocktake_at, False  # the only exact timestamp we hold
        # a stocktake row is a surplus when it sits on the shelf, a shortfall
        # when it was split off as consumed
        quantity = i.count if i.status == STOCK_AVAILABLE else -i.count
    elif i.build_id is not None:
        kind = "produced"
        at = prod_date
        label = prod_ref or f"BO-{i.build_id}"
        url = f"/build-orders/{i.build_id}"
        quantity = i.count
    elif i.po_id is not None:
        kind = "received"
        at = po_date
        label = po_ref or f"PO-{i.po_id}"
        url = f"/purchase-orders/{i.po_id}"
        quantity = i.count
    else:
        kind, quantity = "unknown", i.count
    entries = []
    # How this stock came to exist, when no surviving row reports it: a
    # consumed row keeps the PO it was bought on or the build that made it, and
    # for imported stock that row is all there is.
    if kind in ("consumed", "debt") and i.id not in split_of:
        if i.po_id is not None:
            origin = (
                "received",
                po_date,
                po_ref or f"PO-{i.po_id}",
                "purchase-orders",
                i.po_id,
            )
        elif i.build_id is not None:
            origin = (
                "produced",
                prod_date,
                prod_ref or f"BO-{i.build_id}",
                "build-orders",
                i.build_id,
            )
        else:
            origin = None
        if origin is not None:
            origin_kind, origin_at, origin_label, route, order_id = origin
            entries.append(
                StockLogEntryOut(
                    item_id=i.id,
                    kind=origin_kind,
                    at=origin_at,
                    at_approx=origin_at is not None,
                    quantity=abs(i.count),
                    drawn_down=False,
                    reason="",
                    order_label=origin_label,
                    order_url=f"/{route}/{order_id}",
                    unit_price=i.unit_price,
                    price_basis=i.price_basis,
                )
            )
    entries.append(
        StockLogEntryOut(
            item_id=i.id,
            kind=kind,
            at=at,
            at_approx=approx and at is not None,
            quantity=quantity,
            drawn_down=drawn_down,
            reason=i.stocktake_reason or "",
            order_label=label,
            order_url=url,
            unit_price=i.unit_price,
            price_basis=i.price_basis,
        )
    )
    return entries


def _imported_production_entries(part_id: int) -> list[StockLogEntryOut]:
    """Production the stock rows cannot show: InvenTree deletes fully-used
    stock, so a migrated build's output often left no row behind and the log
    read empty for a part with a decade of builds.

    `BuildOrder.imported_produced` is what InvenTree said the build made; the
    surviving rows are what we can still see. The difference is the missing
    history, emitted as one synthetic entry per build -- same `max(...)`
    baseline `db.produced_qty` uses, so the log agrees with the build page."""
    with db.session() as s:
        rows = s.execute(
            select(
                BuildOrder,
                func.coalesce(func.sum(StockItem.count), 0.0),
            )
            .join(StockItem, StockItem.build_id == BuildOrder.id, isouter=True)
            .where(BuildOrder.part_id == part_id, BuildOrder.imported_produced > 0)
            .group_by(BuildOrder.id)
        ).all()
    entries = []
    for b, from_stock in rows:
        missing = float(b.imported_produced) - (from_stock or 0.0)
        if missing < 1e-9:
            continue
        entries.append(
            StockLogEntryOut(
                item_id=None,  # the stock row is gone; nothing to link to
                kind="produced",
                at=b.start_date,
                at_approx=b.start_date is not None,
                quantity=missing,
                drawn_down=False,
                reason="",
                order_label=b.reference or f"BO-{b.id}",
                order_url=f"/build-orders/{b.id}",
                # the stock is gone, so nothing records what it cost
                unit_price=None,
                price_basis="none",
            )
        )
    return entries


@router.get("/parts/{part_id}/stock-log", response_model=list[StockLogEntryOut])
def list_part_stock_log(part_id: int) -> list[StockLogEntryOut]:
    """What happened to this part's stock, oldest first (the table shows it
    newest first -- sorting is the frontend's).

    Derived from the stock rows themselves -- no event table. ponytail: so
    mutations that overwrite a row in place (set_count, set_item_status, the
    count drawdown during consumption or debt settlement) show only the current
    value, not the steps; add a real event table the day that matters."""
    producer = aliased(BuildOrder)
    eater = aliased(BuildOrder)
    with db.session() as s:
        rows = s.execute(
            select(
                StockItem,
                PurchaseOrder.reference,
                PurchaseOrder.start_date,
                producer.reference,
                producer.start_date,
                eater.reference,
                eater.start_date,
            )
            .join(PurchaseOrder, StockItem.po_id == PurchaseOrder.id, isouter=True)
            .join(producer, StockItem.build_id == producer.id, isouter=True)
            .join(eater, StockItem.consumed_by_build_id == eater.id, isouter=True)
            .where(StockItem.part_id == part_id)
        ).all()
        # what each booked row received: a PO line orders packs of pack_qty
        booked = {
            item_id: qty * pack
            for item_id, qty, pack in s.execute(
                select(Booking.stock_item_id, POLine.quantity, SupplierPart.pack_qty)
                .join(POLine, Booking.po_line_id == POLine.id)
                .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
                .join(StockItem, Booking.stock_item_id == StockItem.id)
                .where(StockItem.part_id == part_id)
            )
        }
    # Orders whose receipt/production is still reported by a row of its own. A
    # consumed row sharing that order is a slice of it (produce_build copies
    # po_id and build_id onto the split), so it must not report that origin a
    # second time.
    survivors = [r[0] for r in rows if r[0].consumed_by_build_id is None]
    reported_pos = {i.po_id for i in survivors if i.po_id is not None}
    reported_builds = {i.build_id for i in survivors if i.build_id is not None}
    split_of = {
        r[0].id
        for r in rows
        if r[0].po_id in reported_pos or r[0].build_id in reported_builds
    }
    entries = [e for r in rows for e in _stock_log_entries(r, booked, split_of)]
    entries += _imported_production_entries(part_id)
    # ids are strictly max+1, so they order events that share a date -- which
    # whole batches do, every row of one order carrying that order's date.
    # Pydantic has widened every `at` to datetime, so order dates (midnight)
    # and a stocktake's exact time compare cleanly.
    # a reconstructed event has no stock row; sort it alongside id 0, which is
    # below every real id, so it leads the batch it belongs to
    dated = [e for e in entries if e.at is not None]
    dated.sort(key=lambda e: (e.at, e.item_id or 0))
    # rows with no order and no stocktake carry no date at all; keep them last,
    # in id order, rather than inventing a position for them
    return dated + sorted(
        (e for e in entries if e.at is None), key=lambda e: e.item_id or 0
    )


@router.get("/parts/{part_id}/consumed-by", response_model=list[PartBuildOut])
def list_part_consumed_by(part_id: int) -> list[PartBuildOut]:
    """Build orders whose component snapshot lists this part, with the quantity
    each requires of it and the stock each has consumed so far."""
    with db.session() as s:
        taken = {
            build_id: total
            for build_id, total in s.execute(
                select(StockItem.consumed_by_build_id, func.sum(StockItem.count))
                .where(
                    StockItem.part_id == part_id,
                    StockItem.status == STOCK_CONSUMED,
                    StockItem.consumed_by_build_id.is_not(None),
                )
                .group_by(StockItem.consumed_by_build_id)
            )
        }
        return [
            PartBuildOut(
                **_build_out(s, b).model_dump(),
                required=qty * b.quantity,
                consumed=taken.get(b.id, 0.0),
            )
            for b, qty in s.execute(
                select(BuildOrder, BuildLine.quantity)
                .join(BuildLine, BuildLine.build_id == BuildOrder.id)
                .where(BuildLine.component_part_id == part_id)
                .order_by(BuildOrder.id)
            )
        ]


# -- bom (nested under a part) ----------------------------------------------


@router.get("/parts/{part_id}/bom", response_model=list[BomLineOut])
def list_bom(part_id: int) -> list[BomLineOut]:
    with db.session() as s:
        rows = db.bom_for(s, part_id)
    return [
        BomLineOut(
            id=line_id,
            component_part_id=comp_id,
            component_sku=comp_sku or "",
            component_description=comp_desc,
            component_virtual=comp_virtual,
            quantity=qty,
            unit_price=price,
            line_price=None if price is None else price * qty,
            price_partial=partial,
        )
        for line_id, comp_id, comp_sku, comp_desc, qty, comp_virtual, price, partial in rows
    ]


@router.post("/parts/{part_id}/bom", response_model=list[BomLineOut], status_code=201)
def add_bom_line(part_id: int, body: BomLineIn) -> list[BomLineOut]:
    _guard(
        db.add_bomline,
        db.next_bomline_id(),
        part_id,
        body.component_part_id,
        body.quantity,
    )
    return list_bom(part_id)


class BomQtyPatch(BaseModel):
    quantity: float


class POLinePatch(BaseModel):
    quantity: int | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, ge=0)


@router.patch("/bom/{line_id}", response_model=OkOut)
def set_bom_quantity(line_id: int, body: BomQtyPatch) -> OkOut:
    _guard(db.edit_bomline_quantity, line_id, body.quantity)
    return OkOut()


@router.delete("/bom/{line_id}", response_model=OkOut)
def remove_bom_line(line_id: int) -> OkOut:
    _guard(db.remove_bomline, line_id)
    return OkOut()


# -- stock ------------------------------------------------------------------


# the build a stock row belongs to: its producer, else the build that owes it
# (a shortfall debt row and its consumed placeholder have no producing build)
_src_build = func.coalesce(StockItem.build_id, StockItem.consumed_by_build_id)
# the consuming build needs its own join: when a row has both a producer and a
# consumer, _src_build names the producer, so nothing else carries this label
_eater = aliased(BuildOrder)


_STOCK_SELECT = (
    select(
        StockItem,
        Part.sku,
        Part.description,
        PurchaseOrder.reference,
        _src_build,
        BuildOrder.reference,
        _eater.reference,
    )
    .join(Part, StockItem.part_id == Part.id)
    .join(PurchaseOrder, StockItem.po_id == PurchaseOrder.id, isouter=True)
    # the build this stock came from: the one that produced it, or
    # (debt/consumed rows have no producer) the one that owes it
    .join(BuildOrder, _src_build == BuildOrder.id, isouter=True)
    .join(_eater, StockItem.consumed_by_build_id == _eater.id, isouter=True)
    .order_by(StockItem.id)
)


def _stock_out(row) -> StockItemOut:
    i, sku, desc, po_ref, src_build, build_ref, eater_ref = row
    return StockItemOut(
        id=i.id,
        part_id=i.part_id,
        sku=sku,
        description=desc,
        count=i.count,
        po_id=i.po_id,
        po_reference=po_ref or "",
        build_id=i.build_id,
        consumed_by_build_id=i.consumed_by_build_id,
        build_reference=build_ref
        or (f"BO-{src_build}" if src_build is not None else ""),
        consumed_by_reference=eater_ref
        or (f"BO-{i.consumed_by_build_id}" if i.consumed_by_build_id else ""),
        status=i.status,
        unit_price=i.unit_price,
        price_basis=i.price_basis,
        stocktake_at=i.stocktake_at,
        stocktake_reason=i.stocktake_reason or "",
    )


def _stock_rows() -> list[StockItemOut]:
    with db.session() as s:
        return [_stock_out(r) for r in s.execute(_STOCK_SELECT)]


@router.get("/stock", response_model=list[StockItemOut])
def list_stock() -> list[StockItemOut]:
    return _stock_rows()


@router.post("/stock/stocktake", response_model=PartOut)
def stocktake(body: StocktakeIn) -> PartOut:
    """Correct a part's counted stock. Returns the part, whose in_stock now
    reflects the count."""
    _guard(
        db.stocktake,
        body.part_id,
        body.count,
        body.reason,
        body.item_id,
        body.build_id,
        body.po_id,
    )
    return get_part(body.part_id)


@router.post("/stock/negative", response_model=PartOut)
def add_negative_stock(body: NegativeStockIn) -> PartOut:
    """Record stock a build used that was never booked in (an import repair)."""
    _guard(
        db.add_negative_stock,
        body.part_id,
        body.quantity,
        body.build_id,
        body.reason,
    )
    return get_part(body.part_id)


@router.post("/stock/{item_id}/settle", response_model=PartOut)
def settle_debt(item_id: int, body: SettleDebtIn) -> PartOut:
    """Pay off a build shortfall out of stock already on the shelf."""
    part_id = get_stock(item_id).part_id
    _guard(db.settle_debt_from_stock, item_id, body.quantity, body.item_id)
    return get_part(part_id)


# registered after /stock/stocktake so the literal path is not read as an id
@router.get("/stock/{item_id}", response_model=StockItemOut)
def get_stock(item_id: int) -> StockItemOut:
    for row in _stock_rows():
        if row.id == item_id:
            return row
    raise HTTPException(status_code=404, detail=f"no stock item {item_id}")


@router.post("/stock", response_model=StockItemOut, status_code=201)
def create_stock(body: StockItemIn) -> StockItemOut:
    new_id = db.next_item_id()
    _guard(db.create_item, new_id, body.part_id)
    return get_stock(new_id)


@router.patch("/stock/{item_id}", response_model=StockItemOut)
def patch_stock(item_id: int, body: StockItemPatch) -> StockItemOut:
    if body.count is not None:
        _guard(db.set_count, item_id, body.count)
    if body.status is not None:
        _guard(db.set_item_status, item_id, body.status)
    return get_stock(item_id)


# -- suppliers --------------------------------------------------------------


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers() -> list[SupplierOut]:
    with db.session() as s:
        return [
            SupplierOut(id=x.id, name=x.name, active=x.active)
            for x in s.scalars(select(Supplier).order_by(Supplier.id))
        ]


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int) -> SupplierOut:
    with db.session() as s:
        x = s.get(Supplier, supplier_id)
    if x is None:
        raise HTTPException(status_code=404, detail=f"no supplier {supplier_id}")
    return SupplierOut(id=x.id, name=x.name, active=x.active)


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(body: SupplierIn) -> SupplierOut:
    new_id = db.next_supplier_id()
    _guard(db.create_supplier, new_id, body.name)
    return get_supplier(new_id)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
def patch_supplier(supplier_id: int, body: SupplierPatch) -> SupplierOut:
    if body.name is not None:
        _guard(db.edit_supplier, supplier_id, body.name)
    if body.active is not None:
        _guard(db.set_supplier_active, supplier_id, body.active)
    return get_supplier(supplier_id)


@router.get("/suppliers/{supplier_id}/parts", response_model=list[SupplierPartOut])
def list_supplier_supplier_parts(supplier_id: int) -> list[SupplierPartOut]:
    return [r for r in _supplier_part_rows() if r.supplier_id == supplier_id]


@router.get(
    "/suppliers/{supplier_id}/purchase-orders", response_model=list[PurchaseOrderOut]
)
def list_supplier_pos(supplier_id: int) -> list[PurchaseOrderOut]:
    with db.session() as s:
        return [
            _po_out(po)
            for po in s.scalars(
                select(PurchaseOrder)
                .where(PurchaseOrder.supplier_id == supplier_id)
                .order_by(PurchaseOrder.id)
            )
        ]


# -- supplier parts ---------------------------------------------------------


def _last_purchase() -> dict[int, tuple[float, date]]:
    """Per supplier part: (price per pack, order date) of its newest priced,
    non-cancelled PO line. Read in one pass; the caller divides by pack_qty."""
    with db.session() as s:
        rows = s.execute(
            select(POLine.supplier_part_id, POLine.price, PurchaseOrder.start_date)
            .join(PurchaseOrder, POLine.po_id == PurchaseOrder.id)
            .where(POLine.price > 0, PurchaseOrder.status != POStatus.CANCELLED)
            .order_by(PurchaseOrder.start_date, PurchaseOrder.id, POLine.id)
        ).all()
    # later rows win, so the last one seen per supplier part is the newest
    return {sp_id: (price, when) for sp_id, price, when in rows}


def _supplier_part_rows() -> list[SupplierPartOut]:
    last = _last_purchase()
    with db.session() as s:
        return [
            SupplierPartOut(
                id=p.id,
                supplier_id=p.supplier_id,
                sku=p.sku,
                part_id=p.part_id,
                part_sku=part_sku or "",
                part_description=part_description or "",
                description=p.description,
                ean=p.ean,
                hyperlink=p.hyperlink,
                pack_qty=p.pack_qty,
                active=p.active,
                last_price=last[p.id][0] / p.pack_qty
                if p.id in last and p.pack_qty > 0
                else None,
                last_po_date=last[p.id][1].isoformat() if p.id in last else None,
            )
            for p, part_sku, part_description in s.execute(
                select(SupplierPart, Part.sku, Part.description)
                .join(Part, SupplierPart.part_id == Part.id)
                .order_by(SupplierPart.id)
            )
        ]


@router.get("/supplier-parts", response_model=list[SupplierPartOut])
def list_supplier_parts() -> list[SupplierPartOut]:
    return _supplier_part_rows()


@router.get("/supplier-parts/{sp_id}", response_model=SupplierPartOut)
def get_supplier_part(sp_id: int) -> SupplierPartOut:
    for row in _supplier_part_rows():
        if row.id == sp_id:
            return row
    raise HTTPException(status_code=404, detail=f"no supplier part {sp_id}")


@router.post("/supplier-parts", response_model=SupplierPartOut, status_code=201)
def create_supplier_part(body: SupplierPartIn) -> SupplierPartOut:
    new_id = db.next_supplier_part_id()
    _guard(
        db.create_supplier_part,
        new_id,
        body.supplier_id,
        body.sku,
        body.part_id,
        description=body.description,
        ean=body.ean,
        hyperlink=body.hyperlink,
        pack_qty=body.pack_qty,
    )
    return get_supplier_part(new_id)


@router.patch("/supplier-parts/{sp_id}", response_model=SupplierPartOut)
def patch_supplier_part(sp_id: int, body: SupplierPartPatch) -> SupplierPartOut:
    if body.active is not None:
        _guard(db.set_supplier_part_active, sp_id, body.active)
    if any(
        v is not None
        for v in (
            body.part_id,
            body.description,
            body.ean,
            body.hyperlink,
            body.pack_qty,
        )
    ):
        current = get_supplier_part(sp_id)
        _guard(
            db.edit_supplier_part,
            sp_id,
            body.part_id if body.part_id is not None else current.part_id,
            description=body.description
            if body.description is not None
            else current.description,
            ean=body.ean if body.ean is not None else current.ean,
            hyperlink=body.hyperlink
            if body.hyperlink is not None
            else current.hyperlink,
            pack_qty=body.pack_qty if body.pack_qty is not None else current.pack_qty,
        )
    return get_supplier_part(sp_id)


@router.get(
    "/supplier-parts/{sp_id}/purchase-orders", response_model=list[PartPurchaseOrderOut]
)
def list_supplier_part_pos(sp_id: int) -> list[PartPurchaseOrderOut]:
    """POs that have at least one line for this supplier part."""
    return _pos_with_qty(POLine.supplier_part_id == sp_id)


# -- purchase orders --------------------------------------------------------


def _po_out(po: PurchaseOrder) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=po.id,
        supplier_id=po.supplier_id,
        reference=po.reference,
        status=po.status,
        start_date=po.start_date,
        end_date=po.end_date,
        delivery_cost=po.delivery_cost,
        supplier_reference=po.supplier_reference,
        description=po.description,
    )


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
def list_pos() -> list[PurchaseOrderOut]:
    with db.session() as s:
        return [
            _po_out(po)
            for po in s.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id))
        ]


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def get_po(po_id: int) -> PurchaseOrderOut:
    with db.session() as s:
        po = s.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail=f"no purchase order {po_id}")
    return _po_out(po)


@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=201)
def create_po(body: PurchaseOrderIn) -> PurchaseOrderOut:
    new_id = db.next_po_id()
    _guard(
        db.create_po,
        new_id,
        body.supplier_id,
        reference=body.reference,
        status=body.status,
        start_date=body.start_date,
        end_date=body.end_date,
        delivery_cost=body.delivery_cost,
        supplier_reference=body.supplier_reference,
        description=body.description,
    )
    return get_po(new_id)


@router.patch("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
def patch_po(po_id: int, body: PurchaseOrderPatch) -> PurchaseOrderOut:
    # end_date can be cleared to null, so "field present in payload"
    # (model_fields_set), not "is not None", decides whether to overwrite.
    # start_date is required: an explicit null is rejected by db.edit_po.
    sent = body.model_fields_set
    if sent & {
        "reference",
        "status",
        "start_date",
        "end_date",
        "delivery_cost",
        "supplier_reference",
        "description",
    }:
        cur = get_po(po_id)
        _guard(
            db.edit_po,
            po_id,
            reference=body.reference if "reference" in sent else cur.reference,
            status=body.status if "status" in sent else cur.status,
            start_date=body.start_date if "start_date" in sent else cur.start_date,
            end_date=body.end_date if "end_date" in sent else cur.end_date,
            delivery_cost=body.delivery_cost
            if "delivery_cost" in sent
            else cur.delivery_cost,
            supplier_reference=body.supplier_reference
            if "supplier_reference" in sent
            else cur.supplier_reference,
            description=body.description if "description" in sent else cur.description,
        )
    return get_po(po_id)


# -- po lines (nested under a po) -------------------------------------------


def _po_lines(po_id: int) -> list[POLineOut]:
    with db.session() as s:
        rows = s.execute(
            select(POLine, SupplierPart.sku, SupplierPart.pack_qty)
            .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
            .where(POLine.po_id == po_id)
            .order_by(POLine.id)
        ).all()
    return [
        POLineOut(
            id=line.id,
            po_id=line.po_id,
            supplier_part_id=line.supplier_part_id,
            supplier_sku=sku,
            pack_qty=pack_qty,
            quantity=line.quantity,
            received=line.received,
            price=line.price,
        )
        for line, sku, pack_qty in rows
    ]


@router.get("/purchase-orders/{po_id}/lines", response_model=list[POLineOut])
def list_po_lines(po_id: int) -> list[POLineOut]:
    return _po_lines(po_id)


@router.post(
    "/purchase-orders/{po_id}/lines",
    response_model=list[POLineOut],
    status_code=201,
)
def add_po_line(po_id: int, body: POLineIn) -> list[POLineOut]:
    _guard(
        db.add_po_line,
        db.next_line_id(),
        po_id,
        body.supplier_part_id,
        body.quantity,
        body.price,
    )
    return _po_lines(po_id)


@router.patch("/po-lines/{line_id}", response_model=OkOut)
def edit_po_line(line_id: int, body: POLinePatch) -> OkOut:
    _guard(db.edit_po_line, line_id, body.quantity, body.price)
    return OkOut()


@router.delete("/po-lines/{line_id}", response_model=OkOut)
def remove_po_line(line_id: int) -> OkOut:
    _guard(db.remove_po_line, line_id)
    return OkOut()


@router.post("/po-lines/{line_id}/book", response_model=OkOut)
def book_po_line(line_id: int, body: BookIn) -> OkOut:
    _guard(db.book_po_line, line_id, db.next_item_id(), body.quantity)
    return OkOut()


@router.post("/purchase-orders/{po_id}/receive-all", response_model=OkOut)
def receive_all(po_id: int) -> OkOut:
    """Receive every line's outstanding quantity into stock in one call."""
    _guard(db.book_po, po_id)
    return OkOut()


# -- build orders -----------------------------------------------------------


def _build_out(s, b: BuildOrder) -> BuildOrderOut:
    return BuildOrderOut(
        id=b.id,
        part_id=b.part_id,
        quantity=b.quantity,
        reference=b.reference,
        status=b.status,
        description=b.description,
        start_date=b.start_date,
        end_date=b.end_date,
        produced=round(db.produced_qty(s, b.id)),
        bom_drifted=db.build_bom_drifted(s, b.id),
    )


@router.get("/build-orders", response_model=list[BuildOrderOut])
def list_builds() -> list[BuildOrderOut]:
    with db.session() as s:
        return [
            _build_out(s, b)
            for b in s.scalars(select(BuildOrder).order_by(BuildOrder.id))
        ]


@router.get("/build-orders/{build_id}", response_model=BuildOrderOut)
def get_build(build_id: int) -> BuildOrderOut:
    with db.session() as s:
        b = s.get(BuildOrder, build_id)
        if b is None:
            raise HTTPException(status_code=404, detail=f"no build order {build_id}")
        return _build_out(s, b)


@router.post("/build-orders", response_model=BuildOrderOut, status_code=201)
def create_build(body: BuildOrderIn) -> BuildOrderOut:
    new_id = db.next_build_id()
    _guard(
        db.create_build,
        new_id,
        body.part_id,
        body.quantity,
        reference=body.reference,
        status=body.status,
        description=body.description,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    return get_build(new_id)


@router.patch("/build-orders/{build_id}", response_model=BuildOrderOut)
def patch_build(build_id: int, body: BuildOrderPatch) -> BuildOrderOut:
    # dates can be cleared to null, so "field present in payload" (model_fields_set),
    # not "is not None", decides whether to overwrite.
    sent = body.model_fields_set
    if sent & {
        "quantity",
        "reference",
        "status",
        "description",
        "start_date",
        "end_date",
    }:
        cur = get_build(build_id)
        _guard(
            db.edit_build,
            build_id,
            body.quantity if "quantity" in sent else cur.quantity,
            reference=body.reference if "reference" in sent else cur.reference,
            status=body.status if "status" in sent else cur.status,
            description=body.description if "description" in sent else cur.description,
            start_date=body.start_date if "start_date" in sent else cur.start_date,
            end_date=body.end_date if "end_date" in sent else cur.end_date,
        )
    return get_build(build_id)


@router.post("/build-orders/{build_id}/produce", response_model=BuildOrderOut)
def produce_build(build_id: int, body: ProduceIn) -> BuildOrderOut:
    """Produce `quantity` assembly units: consume BOM components FIFO for those
    units and add the produced stock. Completes the order when fully produced."""
    _guard(db.produce_build, build_id, body.quantity)
    return get_build(build_id)


@router.get("/build-orders/{build_id}/lines", response_model=list[BuildLineOut])
def list_build_lines(build_id: int) -> list[BuildLineOut]:
    """The build's component snapshot -- the BOM as it stood when it was
    created, which the part's current BOM may no longer match."""
    with db.session() as s:
        b = s.get(BuildOrder, build_id)
        if b is None:
            raise HTTPException(status_code=404, detail=f"no build order {build_id}")
        rows = db.build_lines_for(s, build_id)
        taken: dict[int, float] = {}
        for item in s.scalars(
            select(StockItem).where(
                StockItem.consumed_by_build_id == build_id,
                StockItem.status == STOCK_CONSUMED,
            )
        ):
            taken[item.part_id] = taken.get(item.part_id, 0.0) + item.count
        return [
            BuildLineOut(
                id=line_id,
                component_part_id=comp_id,
                component_sku=comp_sku or "",
                component_description=comp_desc,
                component_virtual=comp_virtual,
                quantity=qty,
                required=qty * b.quantity,
                consumed=taken.get(comp_id, 0.0),
                unit_price=price,
                line_price=None if price is None else price * qty,
                price_partial=partial,
            )
            for line_id, comp_id, comp_sku, comp_desc, qty, comp_virtual, price, partial in rows
        ]


@router.post("/build-orders/{build_id}/resync-lines", response_model=BuildOrderOut)
def resync_build_lines(build_id: int) -> BuildOrderOut:
    """Re-snapshot an active build from its part's current BOM. Rejected once
    the build is Complete or Cancelled."""
    _guard(db.resync_build_lines, build_id)
    return get_build(build_id)


@router.get("/build-orders/{build_id}/stock", response_model=list[StockItemOut])
def list_build_stock(build_id: int) -> list[StockItemOut]:
    """Stock this build touched: the components it consumed
    (consumed_by_build_id) and the output it produced (build_id). A row can be
    both -- output of this build eaten by a later one -- so the two links are
    separate columns; status tells consumed rows from available ones."""
    with db.session() as s:
        return [
            _stock_out(r)
            for r in s.execute(
                _STOCK_SELECT.where(
                    or_(
                        StockItem.build_id == build_id,
                        StockItem.consumed_by_build_id == build_id,
                    )
                )
            )
        ]


# -- settings ---------------------------------------------------------------


@router.get("/settings", response_model=list[SettingOut])
def list_settings() -> list[SettingOut]:
    return [
        SettingOut(key=key, value=db.get_setting(key))
        for key in sorted(db.DOMAIN_DEFAULTS)
    ]


@router.put("/settings/{key}", response_model=SettingOut)
def set_setting(key: str, body: SettingIn) -> SettingOut:
    _guard(db.set_setting, key, body.value)
    return SettingOut(key=key, value=db.get_setting(key))


class StocktakeReasonsOut(BaseModel):
    """Reason suggestions, split by direction; the setting holds them comma
    separated."""

    add: list[str]
    subtract: list[str]


class DeploymentSettingsOut(BaseModel):
    """The settings.toml actually in use, shown read-only on the settings page."""

    path: str
    text: str
    data_file: str


@router.get("/settings/deployment", response_model=DeploymentSettingsOut)
def deployment_settings() -> DeploymentSettingsOut:
    if settings is None:  # uvicorn run directly, no create_app
        raise HTTPException(500, "settings not loaded")
    return DeploymentSettingsOut(
        path=str(settings.path),
        text=settings.text,
        data_file=str(settings.path_of("db", "data_file").resolve()),
    )


@router.get("/settings/stocktake-reasons", response_model=StocktakeReasonsOut)
def stocktake_reasons() -> StocktakeReasonsOut:
    def split(key: str) -> list[str]:
        return [r.strip() for r in db.get_setting(key).split(",") if r.strip()]

    return StocktakeReasonsOut(
        add=split("stocktake.add_reasons"),
        subtract=split("stocktake.subtract_reasons"),
    )


# -- reports ----------------------------------------------------------------


class StockValueRow(BaseModel):
    """One stock item valued. unit_price is None when nothing prices the part
    (then value is None too); basis says how the price was found."""

    item_id: int
    part_id: int
    sku: str
    description: str
    count: float
    status: StockStatus
    unit_price: float | None
    value: float | None
    basis: PriceBasisOut
    basis_po_id: int | None
    basis_po_reference: str
    build_id: int | None  # the build that produced this stock, if any
    build_reference: str  # its human code (e.g. BO-2865); "" when build_id is null
    last_po_date: date | None  # newest non-cancelled PO date pricing this part
    part_active: bool
    part_assembly: bool


class StockValueReport(BaseModel):
    rows: list[StockValueRow]
    total: float  # sum of the priced rows
    unpriced: int  # rows with no price at all
    # priced, but the figure is a floor: a build whose inputs were themselves
    # estimated, or that consumed less than its components asked for
    incomplete: int
    incomplete_value: float  # what those rows contribute to `total`


@router.post("/parts/refresh-prices", response_model=dict[str, int])
def refresh_prices() -> dict[str, int]:
    """Recompute every part's cached price. Normal writes keep prices current;
    this is the backfill/repair path (e.g. after an InvenTree import)."""
    _guard(db.refresh_all_prices)
    with db.session() as s:
        return {
            "priced": len(
                list(
                    s.scalars(select(Part.id).where(Part.estimated_price.is_not(None)))
                )
            )
        }


@router.get("/reports/stock-value", response_model=StockValueReport)
def stock_value_report() -> StockValueReport:
    """Value every stock item from its cached unit_price (see
    db.refresh_stock_price, which owns the rule and keeps the cache current).
    A stock item is worth what its own order paid, not what the part is
    generally worth -- price_basis says which applied."""
    with db.session() as s:
        items = s.execute(
            select(StockItem, Part.sku, Part.description, Part.active, Part.assembly)
            .join(Part, StockItem.part_id == Part.id)
            .order_by(StockItem.id)
        ).all()
        po_references = {
            po_id: reference
            for po_id, reference in s.execute(
                select(PurchaseOrder.id, PurchaseOrder.reference)
            )
        }
        build_references = {
            build_id: reference
            for build_id, reference in s.execute(
                select(BuildOrder.id, BuildOrder.reference)
            )
        }
        # newest PO date per part, same source rule as db.latest_po_price_source
        # (non-cancelled orders only) -- one grouped query, not one per row
        last_po_dates = {
            part_id: date
            for part_id, date in s.execute(
                select(SupplierPart.part_id, func.max(PurchaseOrder.start_date))
                .join(POLine, POLine.supplier_part_id == SupplierPart.id)
                .join(PurchaseOrder, POLine.po_id == PurchaseOrder.id)
                .where(PurchaseOrder.status != POStatus.CANCELLED)
                .group_by(SupplierPart.part_id)
            )
        }

    rows = [
        StockValueRow(
            item_id=item.id,
            part_id=item.part_id,
            sku=sku,
            description=description,
            count=item.count,
            status=item.status,
            unit_price=item.unit_price,
            value=None if item.unit_price is None else item.unit_price * item.count,
            basis=item.price_basis,
            basis_po_id=item.price_po_id,
            basis_po_reference=po_references.get(item.price_po_id, ""),
            build_id=item.build_id,
            build_reference=build_references.get(item.build_id, ""),
            last_po_date=last_po_dates.get(item.part_id),
            part_active=part_active,
            part_assembly=part_assembly,
        )
        for item, sku, description, part_active, part_assembly in items
    ]
    # totals cover stock on hand only: consumed rows are historical build inputs
    # (already counted inside the assembly they went into), so summing them too
    # would double-count. They stay in `rows` for provenance/filtering.
    on_hand = [r for r in rows if r.status == STOCK_AVAILABLE]
    partial = [r for r in on_hand if r.basis == "build_partial"]
    return StockValueReport(
        rows=rows,
        total=sum(r.value for r in on_hand if r.value is not None),
        unpriced=sum(1 for r in on_hand if r.value is None),
        incomplete=len(partial),
        incomplete_value=sum(r.value for r in partial if r.value is not None),
    )


# -- activity log -----------------------------------------------------------


@router.get("/activity", response_model=list[ActivityOut])
def list_activity() -> list[ActivityOut]:
    """The read-only activity log, newest first. refs arrive as structured
    objects (parsed from the row's JSON), never a string."""
    with db.session() as s:
        rows = s.scalars(select(Activity).order_by(Activity.id.desc())).all()
    return [
        ActivityOut(
            id=a.id,
            at=a.at,
            action=a.action,
            message=a.message,
            refs=[ActivityRef(**r) for r in json.loads(a.refs)],
        )
        for a in rows
    ]


# -- search -------------------------------------------------------------


class SearchResult(BaseModel):
    type: Literal["part", "supplier", "supplier_part", "purchase_order", "build_order"]
    id: int
    label: str


SEARCH_LIMIT = 10


def _matches_all_words(q: str, *columns):
    """True if every whitespace-separated word in q appears in at least one of
    columns (each word independently, order-insensitive) -- so "933 DIN"
    matches a "DIN 933" description just as well as "DIN 933" does."""
    words = q.split()
    return and_(*[or_(*[c.ilike(f"%{w}%") for c in columns]) for w in words])


@router.get("/search", response_model=list[SearchResult])
def search(q: str = "", include_inactive: bool = False) -> list[SearchResult]:
    """Live jump-to-record search across parts/suppliers/supplier
    parts/POs/builds by text fields only -- never by id, never stock items."""
    q = q.strip()
    if not q:
        return []
    results: list[SearchResult] = []
    with db.session() as s:
        parts_q = select(Part).where(_matches_all_words(q, Part.sku, Part.description))
        if not include_inactive:
            parts_q = parts_q.where(Part.active)
        for p in s.scalars(parts_q.limit(SEARCH_LIMIT)):
            results.append(
                SearchResult(type="part", id=p.id, label=f"{p.sku} - {p.description}")
            )

        suppliers_q = select(Supplier).where(_matches_all_words(q, Supplier.name))
        if not include_inactive:
            suppliers_q = suppliers_q.where(Supplier.active)
        for x in s.scalars(suppliers_q.limit(SEARCH_LIMIT)):
            results.append(SearchResult(type="supplier", id=x.id, label=x.name))

        sp_q = select(SupplierPart).where(
            _matches_all_words(q, SupplierPart.sku, SupplierPart.description)
        )
        if not include_inactive:
            sp_q = sp_q.where(SupplierPart.active)
        for x in s.scalars(sp_q.limit(SEARCH_LIMIT)):
            results.append(
                SearchResult(
                    type="supplier_part", id=x.id, label=f"{x.sku} - {x.description}"
                )
            )

        po_q = select(PurchaseOrder).where(
            _matches_all_words(
                q, PurchaseOrder.reference, PurchaseOrder.supplier_reference
            )
        )
        for x in s.scalars(po_q.limit(SEARCH_LIMIT)):
            results.append(
                SearchResult(type="purchase_order", id=x.id, label=x.reference)
            )

        build_q = select(BuildOrder).where(_matches_all_words(q, BuildOrder.reference))
        for x in s.scalars(build_q.limit(SEARCH_LIMIT)):
            results.append(SearchResult(type="build_order", id=x.id, label=x.reference))

    return results
