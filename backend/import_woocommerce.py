"""Import WooCommerce orders as sales orders.

Unlike migrate_inventree.py this is a product feature, not a one-shot script:
it runs repeatedly against a live data file, is idempotent, and only ever adds
or updates. WooCommerce owns the commercial facts, so a re-import overwrites
them -- except a booked order's line items, which have already been consumed
against and are left alone (its order-level totals still refresh).
"""

import db
import woocommerce
from models import (
    SalesOrder,
    SalesOrderLine,
    SalesOrderLinePart,
    so_ref,
)


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


def _new_result() -> dict:
    return {"imported": 0, "updated": 0, "skipped": 0, "prefilled": 0, "notes": []}


@db._write
def _import(s, orders: list[dict], labels: dict[str, str], result: dict) -> None:
    notes: list[str] = result["notes"]
    for row in orders:
        # our pk IS the WooCommerce order id, so the lookup is a plain get
        so = s.get(SalesOrder, row["wc_order_id"])
        # booking consumed stock against this order's lines; rewriting them
        # would leave that consumption describing something else. The order's
        # own commercial facts are not what was consumed, so they still refresh
        # -- a discount entered after picking has nowhere else to arrive from
        booked = so is not None and so.booked
        created = row["date_created"]
        if so is None:
            so = SalesOrder(id=row["wc_order_id"])
            s.add(so)
            result["imported"] += 1
        elif booked:
            result["skipped"] += 1
        else:
            result["updated"] += 1
        so.wc_number = row["wc_number"]
        # WooCommerce's slug, verbatim: a store's statuses are whatever its
        # plugins registered, so there is nothing to validate it against
        so.status = row["status"]
        so.customer_name = row["customer_name"]
        so.shipping_country = row["shipping_country"]
        so.shipping_cost = row["shipping_cost"]
        so.fee_total = row["fee_total"]
        # _map_order falls back to "" when the order carries no date
        so.date_created = db.date.fromisoformat(created) if created else None
        s.flush()  # the order must exist before its lines reference it
        if booked:
            continue
        _apply_lines(s, so, row["lines"], notes)
        s.flush()  # ...and the lines must exist before the prefill reads them
        # fill in what the sku mapping knows: only lines with no parts yet, so
        # a re-import never overwrites the user's own work
        result["prefilled"] += db._prefill_so_parts(s, so.id)[0]

    if labels:
        db.set_so_status_labels(s, labels)
    db._activity(
        s,
        "import_woocommerce",
        f"Imported WooCommerce orders: {result['imported']} new, "
        f"{result['updated']} updated, {result['skipped']} skipped, "
        f"{result['prefilled']} part link(s) prefilled",
        [],
    )


def import_orders(base_url, key, secret, after, before=None) -> dict:
    """Fetch and store every order in the window. Returns counts plus notes,
    the same shape the refresh-prices route reports."""
    if not base_url or not key or not secret:
        raise db.InventoryError(
            "WooCommerce is not configured; set the url, key and secret on "
            "the settings page"
        )
    orders = woocommerce.fetch_orders(base_url, key, secret, after, before)
    # what the store calls each of its statuses, including the ones its plugins
    # registered. Refreshed every import: a new plugin means new statuses
    labels = woocommerce.fetch_status_labels(base_url, key, secret)
    result = _new_result()
    _import(orders, labels, result)
    return result
