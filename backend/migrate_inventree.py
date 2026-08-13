"""One-shot migration: drop the current database and rebuild it from an
InvenTree server. This is a migration tool, not a product feature -- it runs
once. Because the target db starts empty, every InvenTree pk is copied verbatim
as our own pk, so ids stay traceable back to InvenTree for later steps and
debugging.

    uv run python migrate_inventree.py <url> <username> <password> [--db PATH]

WARNING: this deletes the target database unconditionally.

Build orders are imported as historical records (part, quantity, status). The
stock each build consumed IS imported (InvenTree's consumed stock), as
STOCK_CONSUMED rows stamped build_id -- the same shape produce_build creates --
so a build's output can be costed from what it actually used. Produced stock is
linked back to its build from InvenTree's own `build` field on the stock row
(stock not produced by a build has none, and falls back to the part estimate).
Available stock is imported directly from
InvenTree's stock endpoint, not replayed as bookings, so PO lines carry ordered
qty/price and stock carries what is on hand -- ready for manual pack_qty
verification.
"""

import argparse
from datetime import date
from pathlib import Path

import db
import inventree
from models import (
    STOCK_CONSUMED,
    BomLine,
    BuildLine,
    BuildOrder,
    BuildStatus,
    Part,
    POLine,
    PriceBasis,
    PurchaseOrder,
    StockItem,
    Supplier,
    SupplierPart,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _drop_redundant_templates(parts: list[dict]) -> list[dict]:
    """Drop InvenTree template parts that are pure grouping nodes: a template
    with variants and no stock of its own, whose reported stock is just the sum
    of its variants'. A template holding its own stock is a real part and stays;
    one with no variants is misconfigured, so it stays too (reported, not
    dropped). Returns the parts to import; the rest of the import then skips
    everything referencing a dropped part, as it does for any missing FK."""
    variants: dict[int, int] = {}
    for p in parts:
        if p["variant_of"]:
            variants[p["variant_of"]] = variants.get(p["variant_of"], 0) + 1
    templates = [p for p in parts if p["is_template"]]
    if not templates:
        return parts
    redundant = {
        p["id"] for p in templates if variants.get(p["id"]) and not p["in_stock"]
    }
    print(f"  {len(templates)} template parts:")
    for p in templates:
        if p["id"] in redundant:
            print(
                f"    dropped: part {p['id']} {p['description']!r} -- stock lives in its variants"
            )
        elif not variants.get(p["id"]):
            print(
                f"    kept: part {p['id']} {p['description']!r} -- no variants, template flag looks unintended"
            )
        else:
            print(
                f"    kept: part {p['id']} {p['description']!r} -- has stock of its own"
            )
    return [p for p in parts if p["id"] not in redundant]


def migrate(url: str, username: str, password: str, db_path: Path) -> None:
    print(f"Fetching from {url} ...")
    parts = inventree.fetch_parts(url, username, password)
    bom = inventree.fetch_bom(url, username, password)
    suppliers = inventree.fetch_suppliers(url, username, password)
    supplier_parts = inventree.fetch_supplier_parts(url, username, password)
    pos = inventree.fetch_purchase_orders(url, username, password)
    po_lines = inventree.fetch_po_lines(url, username, password)
    stock = inventree.fetch_stock(url, username, password)
    builds = inventree.fetch_build_orders(url, username, password)
    build_lines = inventree.fetch_build_lines(url, username, password)
    consumed = inventree.fetch_build_consumption(url, username, password)
    print(
        f"  {len(parts)} parts, {len(bom)} bom, {len(suppliers)} suppliers, "
        f"{len(supplier_parts)} supplier-parts, {len(pos)} POs (non-cancelled), "
        f"{len(po_lines)} PO lines, {len(stock)} stock items, "
        f"{len(builds)} builds (non-cancelled), {len(build_lines)} build lines, "
        f"{len(consumed)} consumed stock rows"
    )
    parts = _drop_redundant_templates(parts)

    for p in (db_path, db_path.with_suffix(".sql")):
        p.unlink(missing_ok=True)
    print(f"Dropped {db_path}; creating fresh schema ...")
    db.init(db_path, export_sql=False)  # export once at the end, not per row

    part_ids: set[int] = set()
    supplier_ids: set[int] = set()
    sp_ids: set[int] = set()
    po_ids: set[int] = set()
    # virtual parts only: InvenTree has no per-build historical rate, so a build
    # line's price is baselined at import from the part's price of the day
    virtual_prices: dict[int, float | None] = {}
    skipped: list[str] = []

    with Session(db._engine) as s, s.begin():
        for p in parts:
            # a virtual part (labour) is never purchased, so nothing else can
            # price it -- carry InvenTree's minimum price over as its hand-set
            # price. One extra request per virtual part; there are only a few.
            price = None
            if p["virtual"]:
                try:
                    price = inventree.fetch_part_price(url, username, password, p["id"])
                except Exception as error:  # noqa: BLE001 - one part must not abort the import
                    skipped.append(f"part {p['id']}: pricing lookup failed ({error})")
            s.add(
                Part(
                    id=p["id"],
                    sku=p["sku"],
                    description=p["description"],
                    active=p["active"],
                    assembly=p["assembly"],
                    virtual=p["virtual"],
                    purchasable=p["purchasable"],
                    estimated_price=price,
                    price_partial=p["virtual"] and price is None,
                )
            )
            part_ids.add(p["id"])
            if p["virtual"]:
                virtual_prices[p["id"]] = price

        for sup in suppliers:
            s.add(Supplier(id=sup["id"], name=sup["name"], active=sup["active"]))
            supplier_ids.add(sup["id"])
        s.flush()  # parts + suppliers exist before things reference them

        for b in bom:
            if b["parent"] not in part_ids or b["component"] not in part_ids:
                skipped.append(f"bom {b['parent']}->{b['component']}: part missing")
                continue
            s.add(
                BomLine(
                    parent_part_id=b["parent"],
                    component_part_id=b["component"],
                    quantity=float(b["quantity"]),
                )
            )

        build_ids = set()
        build_qty: dict[int, int] = {}
        build_produced: dict[int, int] = {}
        for b in builds:
            if b["part"] not in part_ids:
                skipped.append(f"build {b['id']}: assembly part {b['part']} missing")
                continue
            build_ids.add(b["id"])
            qty = float(b["quantity"])
            build_qty[b["id"]] = int(qty) if qty == int(qty) else 0
            build_produced[b["id"]] = int(float(b["completed"]))
            if qty != int(qty):  # assemblies are whole; a fractional build is bad data
                raise ValueError(
                    f"build {b['id']}: fractional quantity {qty}; assemblies must be whole"
                )
            s.add(
                BuildOrder(
                    id=b["id"],
                    part_id=b["part"],
                    reference=b["reference"],
                    description=b["description"],
                    # we have no On Hold for builds: a held build is still
                    # planned, so it lands on Pending
                    status=BuildStatus.PENDING
                    if b["status_text"] == "On Hold"
                    else b["status_text"],
                    quantity=int(qty),
                    # InvenTree's own produced count: it deletes fully-used
                    # stock, so the rows we import cannot reconstruct history
                    imported_produced=int(float(b["completed"])),
                    start_date=_parse_date(b["start_date"]),
                    end_date=_parse_date(b["end_date"]),
                )
            )

        # the BOM each build was created against, which the part's current BOM
        # may no longer match. InvenTree stores the extended quantity; ours is
        # per produced unit, like BomLine.
        s.flush()  # builds must exist before their lines reference them
        for bl in build_lines:
            if bl["build"] not in build_ids:
                skipped.append(f"build line {bl['id']}: build {bl['build']} missing")
                continue
            if bl["part"] not in part_ids:
                skipped.append(f"build line {bl['id']}: part {bl['part']} missing")
                continue
            per_build = build_qty.get(bl["build"], 0)
            if per_build <= 0:
                skipped.append(f"build line {bl['id']}: build quantity unusable")
                continue
            s.add(
                BuildLine(
                    id=bl["id"],
                    build_id=bl["build"],
                    component_part_id=bl["part"],
                    quantity=float(bl["quantity"]) / per_build,
                    # baseline the virtual (labour) rate at what the part is
                    # worth today: InvenTree keeps no historical rate per build,
                    # so this is the best figure available. Once stamped it is
                    # frozen, like a build created in the app.
                    unit_price=virtual_prices.get(bl["part"]),
                )
            )

        for sp in supplier_parts:
            if sp["supplier"] not in supplier_ids or sp["part"] not in part_ids:
                skipped.append(f"supplier-part {sp['id']}: supplier/part missing")
                continue
            s.add(
                SupplierPart(
                    id=sp["id"],
                    supplier_id=sp["supplier"],
                    part_id=sp["part"],
                    sku=sp["sku"],
                    description=sp["description"],
                    hyperlink=sp["hyperlink"],
                    pack_qty=sp["pack_qty"],
                )
            )
            sp_ids.add(sp["id"])

        for po in pos:
            if po["supplier"] not in supplier_ids:
                skipped.append(f"PO {po['id']}: supplier {po['supplier']} missing")
                continue
            # both are required here but optional in InvenTree: an undated order
            # cannot be ranked for "latest price", so fall back to the creation
            # date, then to today; an unreferenced one gets our PO-<pk> code
            start = _parse_date(po["start_date"])
            if start is None:
                start = _parse_date(po.get("creation_date"))
                if start is not None:
                    skipped.append(
                        f"PO {po['id']}: no start date, used creation date {start}"
                    )
            if start is None:
                start = date.today()
                skipped.append(f"PO {po['id']}: no start date, defaulted to today")
            s.add(
                PurchaseOrder(
                    id=po["id"],
                    supplier_id=po["supplier"],
                    reference=po["reference"].strip() or f"PO-{po['id']:04d}",
                    supplier_reference=po["supplier_reference"],
                    description=po["description"],
                    status=po["status_text"],
                    start_date=start,
                    end_date=_parse_date(po["end_date"]),
                )
            )
            po_ids.add(po["id"])
        s.flush()  # supplier-parts + POs exist before lines/stock reference them

        for line in po_lines:
            if line["order"] not in po_ids or line["supplier_part"] not in sp_ids:
                skipped.append(f"PO line {line['id']}: order/supplier-part missing")
                continue
            s.add(
                POLine(
                    id=line["id"],
                    po_id=line["order"],
                    supplier_part_id=line["supplier_part"],
                    quantity=int(float(line["quantity"])),
                    received=float(line["received"]),
                    price=line["price"],
                )
            )

        for item in stock:
            if item["part"] not in part_ids:
                skipped.append(f"stock {item['id']}: part {item['part']} missing")
                continue
            # link stock back to its PO, but only if that PO survived (cancelled
            # POs were dropped); otherwise leave po_id null
            po_id = item["purchase_order"] if item["purchase_order"] in po_ids else None
            # InvenTree records which build produced a built item; keep it so the
            # stock can be costed from what that build consumed
            build_id = item["build"] if item["build"] in build_ids else None
            s.add(
                StockItem(
                    id=item["id"],
                    part_id=item["part"],
                    count=float(item["quantity"]),
                    po_id=po_id,
                    build_id=build_id,
                )
            )

        # stock each build consumed: what its output actually cost. Imported as
        # STOCK_CONSUMED rows stamped consumed_by_build_id, the same shape
        # produce_build creates, so build costing works on historical builds too.
        for item in consumed:
            if item["part"] not in part_ids:
                skipped.append(f"consumed {item['id']}: part {item['part']} missing")
                continue
            if item["consumed_by"] not in build_ids:
                skipped.append(
                    f"consumed {item['id']}: build {item['consumed_by']} missing"
                )
                continue
            po_id = item["purchase_order"] if item["purchase_order"] in po_ids else None
            s.add(
                StockItem(
                    id=item["id"],
                    part_id=item["part"],
                    count=float(item["quantity"]),
                    po_id=po_id,
                    # keep both links: consumed assembly stock was produced by
                    # one build and eaten by another, and the producing link is
                    # what counts that build's output (db.produced_qty)
                    build_id=item["build"] if item["build"] in build_ids else None,
                    consumed_by_build_id=item["consumed_by"],
                    status=STOCK_CONSUMED,
                )
            )

        # virtual components (labour) are never stocked, so InvenTree has no
        # consumed row for them -- but the build did use them and the cost is
        # real. Synthesise one per virtual build line, at the rate that line
        # snapshotted, so historical builds cost the same way new ones do.
        s.flush()
        next_stock_id = (s.scalar(select(func.max(StockItem.id))) or 0) + 1
        for bl in s.scalars(select(BuildLine)):
            if bl.component_part_id not in virtual_prices:
                continue
            # labour is spent per unit MADE, never per unit ordered: a build
            # that has produced nothing has consumed nothing
            made = build_produced.get(bl.build_id, 0)
            if made <= 0:
                continue
            s.add(
                StockItem(
                    id=next_stock_id,
                    part_id=bl.component_part_id,
                    count=bl.quantity * made,
                    consumed_by_build_id=bl.build_id,
                    status=STOCK_CONSUMED,
                    unit_price=bl.unit_price,
                    price_basis=PriceBasis.VIRTUAL
                    if bl.unit_price is not None
                    else PriceBasis.NONE,
                )
            )
            next_stock_id += 1

    db.refresh_all_prices()
    db.export()
    print(f"Done. Data written to {db_path} and {db_path.with_suffix('.sql')}.")
    if virtual_prices:
        rates = ", ".join(
            f"part {pid}={'unpriced' if price is None else price}"
            for pid, price in sorted(virtual_prices.items())
        )
        print(
            f"Virtual components baselined at today's rate ({rates}); InvenTree "
            "keeps no historical rate per build."
        )
    if skipped:
        print(f"\n{len(skipped)} rows skipped/adjusted (review these):")
        for line in skipped:
            print(f"  - {line}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Drop the db and import from InvenTree.")
    ap.add_argument("url", help="InvenTree base URL, e.g. http://192.168.1.10")
    ap.add_argument("username")
    ap.add_argument("password")
    ap.add_argument(
        "--db", type=Path, default=Path("inventory.db"), help="database path"
    )
    args = ap.parse_args()
    migrate(args.url, args.username, args.password, args.db)


if __name__ == "__main__":
    main()
