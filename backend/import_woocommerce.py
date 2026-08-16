"""Import WooCommerce orders as sales orders.

Unlike migrate_inventree.py this is a product feature, not a one-shot script:
it runs repeatedly against a live data file, is idempotent, and only ever adds
or updates. WooCommerce owns the commercial facts, so a re-import overwrites
them -- except on a booked order, which has already consumed stock and is left
alone.
"""

import db
import woocommerce
from models import SalesOrder, SalesOrderLine, SalesOrderLinePart, so_ref


def _apply_lines(s, so: SalesOrder, lines: list[dict], notes: list[str]) -> None:
    """Replace an order's line items with what WooCommerce now reports.

    Part links are the user's work, not WooCommerce's, so they are preserved
    across a re-import by matching on wc_line_id. A line that vanished from
    WooCommerce takes its links with it, which is reported rather than done
    silently."""
    existing = {line.wc_line_id: line for line in db.so_lines_for(s, so.id)}
    seen = set()
    next_id = (s.scalar(db.select(db.func.max(SalesOrderLine.id))) or 0) + 1
    for row in lines:
        seen.add(row["wc_line_id"])
        line = existing.get(row["wc_line_id"])
        if line is None:
            line = SalesOrderLine(id=next_id, so_id=so.id, wc_line_id=row["wc_line_id"])
            s.add(line)
            next_id += 1
        line.sku = row["sku"]
        line.description = row["description"]
        line.unit_price = row["unit_price"]
        line.quantity = row["quantity"]

    for wc_line_id, line in existing.items():
        if wc_line_id in seen:
            continue
        links = s.scalars(
            db.select(SalesOrderLinePart).where(SalesOrderLinePart.line_id == line.id)
        ).all()
        if links:
            notes.append(
                f"{so_ref(so.id)}: line '{line.description}' no longer exists in "
                f"WooCommerce; dropped {len(links)} part link(s)"
            )
        for link in links:
            s.delete(link)
        s.delete(line)


@db._write
def _import(s, orders: list[dict], result: dict) -> None:
    notes: list[str] = result["notes"]
    next_so_id = (s.scalar(db.select(db.func.max(SalesOrder.id))) or 0) + 1
    for row in orders:
        so = db.get_so_by_wc_id(s, row["wc_order_id"])
        if so is not None and so.booked:
            # booking consumed stock against these lines; rewriting them would
            # leave that consumption describing something else
            result["skipped"] += 1
            continue
        if so is None:
            so = SalesOrder(id=next_so_id, wc_order_id=row["wc_order_id"])
            s.add(so)
            next_so_id += 1
            result["imported"] += 1
        else:
            result["updated"] += 1
        so.wc_number = row["wc_number"]
        so.status = row["status"]
        so.customer_name = row["customer_name"]
        so.shipping_country = row["shipping_country"]
        so.shipping_cost = row["shipping_cost"]
        so.date_created = db.date.fromisoformat(row["date_created"])
        s.flush()  # the order must exist before its lines reference it
        _apply_lines(s, so, row["lines"], notes)

    db._activity(
        s,
        "import_woocommerce",
        f"Imported WooCommerce orders: {result['imported']} new, "
        f"{result['updated']} updated, {result['skipped']} skipped",
        [],
    )


def import_orders(base_url, key, secret, after, before=None) -> dict:
    """Fetch and store every order in the window. Returns counts plus notes,
    the same shape the refresh-prices route reports."""
    if not base_url or not key or not secret:
        raise db.InventoryError(
            "WooCommerce is not configured; set url, key and secret in the "
            "[woocommerce] section of settings.toml"
        )
    orders = woocommerce.fetch_orders(base_url, key, secret, after, before)
    result = {"imported": 0, "updated": 0, "skipped": 0, "notes": []}
    _import(orders, result)
    return result
