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
from sqlalchemy import and_, func, or_, select

router = APIRouter(prefix="/api")


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
    status: StockStatus
    unit_price: float | None  # what THIS stock is worth per item
    price_basis: PriceBasisOut


class StockItemIn(BaseModel):
    part_id: int


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


class PurchaseOrderIn(BaseModel):
    supplier_id: int
    reference: str = ""
    status: POStatus = POStatus.PENDING
    # the order date: defaults to today rather than being required of the caller
    start_date: date = Field(default_factory=date.today)
    end_date: date | None = None
    delivery_cost: float = 0.0
    supplier_reference: str = ""


class PurchaseOrderPatch(BaseModel):
    reference: str | None = None
    status: POStatus | None = None
    start_date: date | None = None
    end_date: date | None = None
    delivery_cost: float | None = None
    supplier_reference: str | None = None


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
    start_date: date | None
    end_date: date | None
    produced: int  # whole assemblies already produced into stock
    bom_drifted: bool  # the part's BOM no longer matches this build's snapshot


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
    status: BuildStatus = BuildStatus.PENDING
    start_date: date | None = None
    end_date: date | None = None


class BuildOrderPatch(BaseModel):
    quantity: int | None = Field(default=None, gt=0)
    reference: str | None = None
    status: BuildStatus | None = None
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


def _part_out(p: Part) -> PartOut:
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
    )


@router.get("/parts", response_model=list[PartOut])
def list_parts() -> list[PartOut]:
    with db.session() as s:
        return [_part_out(p) for p in s.scalars(select(Part).order_by(Part.id))]


@router.get("/parts/{part_id}", response_model=PartOut)
def get_part(part_id: int) -> PartOut:
    with db.session() as s:
        part = s.get(Part, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail=f"no part {part_id}")
    return _part_out(part)


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


@router.get("/parts/{part_id}/purchase-orders", response_model=list[PurchaseOrderOut])
def list_part_pos(part_id: int) -> list[PurchaseOrderOut]:
    """POs that ordered this part (through any of its supplier parts)."""
    with db.session() as s:
        return [
            _po_out(po)
            for po in s.scalars(
                select(PurchaseOrder)
                .join(POLine, POLine.po_id == PurchaseOrder.id)
                .join(SupplierPart, POLine.supplier_part_id == SupplierPart.id)
                .where(SupplierPart.part_id == part_id)
                .distinct()
                .order_by(PurchaseOrder.id)
            )
        ]


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


def _stock_rows() -> list[StockItemOut]:
    with db.session() as s:
        return [
            StockItemOut(
                id=i.id,
                part_id=i.part_id,
                sku=sku,
                description=desc,
                count=i.count,
                po_id=i.po_id,
                po_reference=po_ref or "",
                build_id=i.build_id,
                consumed_by_build_id=i.consumed_by_build_id,
                status=i.status,
                unit_price=i.unit_price,
                price_basis=i.price_basis,
            )
            for i, sku, desc, po_ref in s.execute(
                select(StockItem, Part.sku, Part.description, PurchaseOrder.reference)
                .join(Part, StockItem.part_id == Part.id)
                .join(
                    PurchaseOrder,
                    StockItem.po_id == PurchaseOrder.id,
                    isouter=True,
                )
                .order_by(StockItem.id)
            )
        ]


@router.get("/stock", response_model=list[StockItemOut])
def list_stock() -> list[StockItemOut]:
    return _stock_rows()


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
    "/supplier-parts/{sp_id}/purchase-orders", response_model=list[PurchaseOrderOut]
)
def list_supplier_part_pos(sp_id: int) -> list[PurchaseOrderOut]:
    """POs that have at least one line for this supplier part."""
    with db.session() as s:
        return [
            _po_out(po)
            for po in s.scalars(
                select(PurchaseOrder)
                .join(POLine, POLine.po_id == PurchaseOrder.id)
                .where(POLine.supplier_part_id == sp_id)
                .distinct()
                .order_by(PurchaseOrder.id)
            )
        ]


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
        start_date=body.start_date,
        end_date=body.end_date,
    )
    return get_build(new_id)


@router.patch("/build-orders/{build_id}", response_model=BuildOrderOut)
def patch_build(build_id: int, body: BuildOrderPatch) -> BuildOrderOut:
    # dates can be cleared to null, so "field present in payload" (model_fields_set),
    # not "is not None", decides whether to overwrite.
    sent = body.model_fields_set
    if sent & {"quantity", "reference", "status", "start_date", "end_date"}:
        cur = get_build(build_id)
        _guard(
            db.edit_build,
            build_id,
            body.quantity if "quantity" in sent else cur.quantity,
            reference=body.reference if "reference" in sent else cur.reference,
            status=body.status if "status" in sent else cur.status,
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
            StockItemOut(
                id=i.id,
                part_id=i.part_id,
                sku=sku,
                description=desc,
                count=i.count,
                po_id=i.po_id,
                po_reference=po_ref or "",
                build_id=i.build_id,
                consumed_by_build_id=i.consumed_by_build_id,
                status=i.status,
                unit_price=i.unit_price,
                price_basis=i.price_basis,
            )
            for i, sku, desc, po_ref in s.execute(
                select(StockItem, Part.sku, Part.description, PurchaseOrder.reference)
                .join(Part, StockItem.part_id == Part.id)
                .join(
                    PurchaseOrder,
                    StockItem.po_id == PurchaseOrder.id,
                    isouter=True,
                )
                .where(
                    or_(
                        StockItem.build_id == build_id,
                        StockItem.consumed_by_build_id == build_id,
                    )
                )
                .order_by(StockItem.id)
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
            select(StockItem, Part.sku, Part.description, Part.active)
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
        )
        for item, sku, description, part_active in items
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
