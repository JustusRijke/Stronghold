"""Integration tests driving the real flows: parts -> stock -> purchasing ->
booking -> export -> restore."""

import json
import sqlite3
from datetime import date

import db
import pytest
from models import (
    STOCK_AVAILABLE,
    STOCK_CONSUMED,
    Activity,
    Booking,
    BuildLine,
    Part,
    POLine,
    PurchaseOrder,
    StockItem,
)
from sqlalchemy import select


def test_parts_and_stock_flow(database):
    part_id = db.next_part_id()
    db.create_part(part_id, "BOLT-M3", "M3 bolt")
    db.edit_part(part_id, "M3 hex bolt")
    item_id = db.next_item_id()
    db.create_item(item_id, part_id)
    db.set_count(item_id, 7)  # grid edits set the count directly -> logged
    db.set_count(item_id, 3)
    db.set_item_status(item_id, "Consumed by build order")
    db.set_part_active(part_id, False)
    with db.session() as s:
        part = s.get(Part, part_id)
        item = s.get(StockItem, item_id)
        assert part.description == "M3 hex bolt"
        assert not part.active
        assert item.count == 3
        assert item.status == "Consumed by build order"
        # manual qty adjustments are logged (the 3->7 and 7->3 changes)
        adj = s.scalars(select(Activity).where(Activity.action == "set_count")).all()
        assert len(adj) == 2 and "Adjusted stock" in adj[0].message
    sql = database.read_text(encoding="utf-8")
    assert "INSERT INTO parts" in sql
    assert "INSERT INTO stock_items" in sql


def test_po_defaults_reference_and_date(database):
    """A new PO always has a reference and an order date: without a date it
    cannot be ranked as "latest", so its price would never win."""
    db.create_part(1, "BOLT-M3", "M3 bolt")
    db.create_supplier(1, "Acme Corp")
    db.create_supplier_part(1, 1, "A-100", 1, pack_qty=100)
    db.create_po(7, 1, start_date=date(2026, 1, 1))
    db.add_po_line(db.next_line_id(), 7, 1, 1, 8.0)
    with db.session() as s:
        po = s.get(PurchaseOrder, 7)
        assert po.reference == "PO-0007"  # defaulted from the pk
        assert s.get(Part, 1).estimated_price == 0.08
    # a later order, created with no date at all, must still win on price
    db.create_po(8, 1)
    db.add_po_line(db.next_line_id(), 8, 1, 1, 1.0)
    with db.session() as s:
        assert s.get(PurchaseOrder, 8).start_date == date.today()
        assert s.get(Part, 1).estimated_price == 0.01
    with pytest.raises(db.InventoryError):
        db.edit_po(8, reference="", status="Pending", start_date=date.today())


def test_purchasing_flow(database):
    db.create_part(1, "BOLT-M3", "M3 bolt")
    db.create_supplier(1, "Acme Corp")
    db.create_supplier_part(1, 1, "A-100", 1, ean="123", pack_qty=10)
    db.create_po(1, 1, reference="restock")
    line_id = db.next_line_id()
    db.add_po_line(line_id, 1, 1, 20, 0.05)
    line2 = db.next_line_id()
    db.add_po_line(line2, 1, 1, 5, 0.05)
    db.edit_po_line(line2, 8)
    with db.session() as s:
        assert s.get(POLine, line2).quantity == 8
    # editing the price repoints the part's cached estimate (price / pack_qty)
    db.edit_po_line(line2, price=0.20)
    with db.session() as s:
        assert s.get(POLine, line2).price == 0.20
        assert s.get(Part, 1).estimated_price == 0.02
    db.remove_po_line(line2)
    with db.session() as s:
        assert s.get(POLine, line2) is None
    item_id = db.next_item_id()
    db.book_po_line(line_id, item_id, 20)
    with db.session() as s:
        item = s.get(StockItem, item_id)
        booking = s.get(Booking, item_id)
        line = s.get(POLine, line_id)
        assert item.count == 200  # 20 packs * pack_qty 10
        assert item.part_id == 1
        assert item.po_id == 1  # stock linked back to its PO
        assert line.received == 20  # in ordered units, not expanded count
        assert booking.po_line_id == line_id
    # regression: a booked line must not be removable
    with pytest.raises(db.InventoryError):
        db.remove_po_line(line_id)
    # PO auto-completed (fully received): further line/status edits are locked,
    # and a completed order can't be cancelled
    with db.session() as s:
        assert s.get(PurchaseOrder, 1).status == "Complete"
    with pytest.raises(db.InventoryError):
        db.add_po_line(db.next_line_id(), 1, 1, 1, 0.0)
    with pytest.raises(db.InventoryError):
        db.edit_po_line(line_id, 999)
    with pytest.raises(db.InventoryError):
        db.edit_po(1, reference="restock", status="Cancelled")
    sql = database.read_text(encoding="utf-8")
    assert "INSERT INTO bookings" in sql


def test_bom_flow(database):
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP-A", "component a")
    db.create_part(3, "COMP-B", "component b")
    # a non-assembly parent is rejected
    with pytest.raises(db.InventoryError):
        db.add_bomline(db.next_bomline_id(), 1, 2, 1.0)
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 2.0)
    db.add_bomline(db.next_bomline_id(), 1, 3, 0.5)  # fractional qty allowed
    # duplicate component, self-reference, and non-positive qty all rejected
    with pytest.raises(db.InventoryError):
        db.add_bomline(db.next_bomline_id(), 1, 2, 1.0)
    with pytest.raises(db.InventoryError):
        db.add_bomline(db.next_bomline_id(), 1, 1, 1.0)
    with pytest.raises(db.InventoryError):
        db.add_bomline(db.next_bomline_id(), 1, 3, 0.0)
    # cannot unmark an assembly that still has a bom
    with pytest.raises(db.InventoryError):
        db.set_part_assembly(1, False)
    with db.session() as s:
        bom = {c: (lid, q) for lid, c, _sku, _desc, q, _v, *_price in db.bom_for(s, 1)}
    db.edit_bomline_quantity(bom[2][0], 5.0)
    db.remove_bomline(bom[3][0])
    with db.session() as s:
        lines = db.bom_for(s, 1)
        assert len(lines) == 1
        assert lines[0][1] == 2 and lines[0][4] == 5.0
    sql = database.read_text(encoding="utf-8")
    assert "INSERT INTO bom_lines" in sql


def test_build_flow(database):
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP-A", "component a")
    db.create_part(3, "COMP-B", "component b")
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 2.0)  # 2x comp-a per assembly
    db.add_bomline(db.next_bomline_id(), 1, 3, 1.0)  # 1x comp-b per assembly

    # component stock: comp-a split over two items (FIFO must span both)
    db.create_item(1, 2)
    db.set_count(1, 3)
    db.create_item(2, 2)
    db.set_count(2, 10)
    db.create_item(3, 3)
    db.set_count(3, 5)

    # building a non-assembly part is rejected
    with pytest.raises(db.InventoryError):
        db.create_build(db.next_build_id(), 2, 1)
    build_id = db.next_build_id()
    db.create_build(build_id, 1, 4)  # need 8 comp-a, 4 comp-b

    # comp-c is added to the BOM AFTER the build was created, so it is not in
    # the build's snapshot: the build still consumes what it was created against
    db.create_part(4, "COMP-C", "component c")
    db.add_bomline(db.next_bomline_id(), 1, 4, 1.0)
    db.create_item(4, 4)
    db.set_count(4, 3)
    with db.session() as s:
        assert db.build_bom_drifted(s, build_id)

    # resync pulls the new component into the snapshot (allowed: still active)
    db.resync_build_lines(build_id)
    with db.session() as s:
        assert not db.build_bom_drifted(s, build_id)
        assert len(db.build_lines_for(s, build_id)) == 3

    # produce 3 of 4: consumes 6 comp-a, 3 comp-b, 3 comp-c; not yet Complete
    db.produce_build(build_id, 3)
    with db.session() as s:
        assert s.get(StockItem, 1).count == 0  # first comp-a item drained (FIFO)
        assert s.get(StockItem, 2).count == 7  # then 3 more of 6 taken from second
        assert s.get(StockItem, 3).count == 2  # 3 comp-b consumed of 5
        assert s.get(StockItem, 4).count == 0  # 3 comp-c consumed of 3
        # consumed stock is preserved as new rows, not destroyed
        consumed = s.scalars(
            select(StockItem).where(
                StockItem.status == "Consumed by build order",
                StockItem.consumed_by_build_id == build_id,
            )
        ).all()
        assert sum(c.count for c in consumed) == 12  # 6 comp-a + 3 comp-b + 3 comp-c
        assert db.produced_qty(s, build_id) == 3
        assert db.get_build(s, build_id).status != "Complete"
        # the produce is recorded in the activity log with structured refs
        act = s.scalars(
            select(Activity).where(Activity.action == "produce_build")
        ).one()
        assert "produced 3x an assembly" in act.message
        refs = json.loads(act.refs)
        assert {"type": "build", "id": build_id} in [
            {"type": r["type"], "id": r["id"]} for r in refs
        ]
        assert any(r["type"] == "stock" for r in refs)

    # producing more than remaining is rejected (1 left)
    with pytest.raises(db.InventoryError):
        db.produce_build(build_id, 2)

    # build quantity may not drop below what's already produced (3)
    with pytest.raises(db.InventoryError):
        db.edit_build(build_id, 2)

    # shortage does not block: comp-c is now empty, produce the last unit anyway.
    # The missing unit becomes a debt: consumed at the part's estimate plus a
    # negative Available row stamped with the build.
    db.produce_build(build_id, 1)
    with db.session() as s:
        assert s.get(StockItem, 4).count == 0  # the real comp-c stock, drained
        # the full BOM quantity is consumed now, the last unit on credit
        cc_consumed = s.scalars(
            select(StockItem).where(
                StockItem.status == "Consumed by build order",
                StockItem.part_id == 4,
            )
        ).all()
        assert sum(c.count for c in cc_consumed) == 4
        debt = s.scalars(
            select(StockItem).where(
                StockItem.part_id == 4,
                StockItem.status == "Available",
                StockItem.count < 0,
            )
        ).one()
        assert debt.count == -1
        assert debt.consumed_by_build_id == build_id
        # never the producing build -- that would inflate produced_qty
        assert debt.build_id is None
        assert db.produced_qty(s, build_id) == 4
        assert db.get_build(s, build_id).status == "Complete"

    # a completed build cannot be produced again
    with pytest.raises(db.InventoryError):
        db.produce_build(build_id, 1)

    # regression: a build's output eaten by a LATER build still counts as
    # produced. Production is history; it must not shrink as stock is used up.
    db.create_part(5, "TOP", "a top-level assembly")
    db.set_part_assembly(5, True)
    db.add_bomline(db.next_bomline_id(), 5, 1, 2.0)  # 2x the assembly above
    top_id = db.next_build_id()
    db.create_build(top_id, 5, 2)  # eats 4 of build_id's 4 produced units
    db.produce_build(top_id, 2)
    with db.session() as s:
        assert db.produced_qty(s, build_id) == 4  # not 0, though all 4 were eaten
        assert db.produced_qty(s, top_id) == 2
        eaten = s.scalars(
            select(StockItem).where(StockItem.consumed_by_build_id == top_id)
        ).all()
        # the consumed rows keep pointing at the build that PRODUCED them
        assert {c.build_id for c in eaten} == {build_id}
    sql = database.read_text(encoding="utf-8")
    assert "INSERT INTO build_orders" in sql


def test_build_shortfall_settled_by_purchase(database):
    """A build short of a component carries the debt as negative stock. When the
    parts are received the debt clears and the build's consumption -- and so the
    assembly it produced -- is repriced from the estimate to what was paid."""
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP", "a component")
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 4.0)  # 4x comp per assembly
    db.create_supplier(1, "Acme")
    db.create_supplier_part(1, 1, "C-1", 2, pack_qty=1)
    db.create_po(1, 1)
    line_id = db.next_line_id()
    db.add_po_line(line_id, 1, 1, 10, 3.0)  # estimate for comp becomes 3.00

    # only 1 of the 4 needed is on the shelf: produce anyway, 3 go on credit
    db.create_item(1, 2)
    db.set_count(1, 1)
    build_id = db.next_build_id()
    db.create_build(build_id, 1, 1)
    db.produce_build(build_id, 1)
    with db.session() as s:
        debt = s.scalars(
            select(StockItem).where(StockItem.part_id == 2, StockItem.count < 0)
        ).one()
        assert debt.count == -3
        assert debt.unit_price == 3.0  # the part's estimate
        assert debt.price_basis == "estimate"
        # the assembly is costed in full, but on an estimate
        assert db.build_unit_cost(s, build_id) == (12.0, False)

    # the missing parts arrive, cheaper than estimated
    db.edit_po_line(line_id, price=2.0)
    item_id = db.next_item_id()
    db.book_po_line(line_id, item_id, 10)
    with db.session() as s:
        assert s.get(StockItem, debt.id).count == 0  # debt paid off
        # settled units went straight into the assembly, never onto the shelf
        assert s.get(StockItem, item_id).count == 7
        settled = s.scalars(
            select(StockItem).where(
                StockItem.consumed_by_build_id == build_id,
                StockItem.po_id.is_not(None),
            )
        ).one()
        assert settled.count == 3
        assert settled.price_basis == "po"
        assert settled.unit_price == 2.0
        # 3 actually paid at 2.00, plus the 1 shelf unit whose estimate the
        # repriced PO line moved to 2.00 as well
        assert db.build_unit_cost(s, build_id) == (8.0, False)
        produced = s.scalars(
            select(StockItem).where(StockItem.build_id == build_id)
        ).one()
        assert produced.unit_price == 8.0  # the assembly repriced itself
        # the receipt says what it paid off, and links the build
        act = s.scalars(
            select(Activity).where(Activity.action == "settle_stock_debt")
        ).one()
        assert "settled a shortfall" in act.message
        assert ("build", build_id) in [
            (r["type"], r["id"]) for r in json.loads(act.refs)
        ]


def test_shortfall_settled_from_stock(database):
    """A part holding both a debt and available stock settles the two by hand,
    repricing the build off the stock that actually paid for it."""
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP", "a component")
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 4.0)
    db.create_supplier(1, "Acme")
    db.create_supplier_part(1, 1, "C-1", 2, pack_qty=1)
    db.create_po(1, 1)
    line_id = db.next_line_id()
    db.add_po_line(line_id, 1, 1, 10, 3.0)  # comp estimate 3.00

    build_id = db.next_build_id()
    db.create_build(build_id, 1, 1)
    db.produce_build(build_id, 1)  # nothing on the shelf: all 4 on credit
    with db.session() as s:
        debt_id = (
            s.scalars(
                select(StockItem).where(StockItem.part_id == 2, StockItem.count < 0)
            )
            .one()
            .id
        )

    # the stock turns up on a PO after the fact, at a real price
    db.edit_po_line(line_id, price=2.0)
    db.book_po_line(line_id, db.next_item_id(), 10)  # settlement needs a debt
    with db.session() as s:
        assert s.get(StockItem, debt_id).count == 0

    # a second shortfall, this time settled from what is already on the shelf
    db.add_negative_stock(2, 2.0, build_id)
    with db.session() as s:
        debt2 = s.scalars(
            select(StockItem).where(StockItem.part_id == 2, StockItem.count < 0)
        ).one()
        debt2_id, shelf_before = debt2.id, db.on_hand(s, 2)
    db.settle_debt_from_stock(debt2_id, 2.0)
    with db.session() as s:
        assert s.get(StockItem, debt2_id).count == 0
        # the debt row nets out of on_hand, so settling it leaves the sum alone;
        # what moved is real stock off the shelf into the build
        assert db.on_hand(s, 2) == shelf_before
        settled = s.scalars(
            select(StockItem).where(
                StockItem.consumed_by_build_id == build_id,
                StockItem.count == 2.0,
                StockItem.po_id.is_not(None),
            )
        ).one()
        assert settled.unit_price == 2.0  # what the shelf stock cost
        assert settled.price_basis == "po"


def test_shortfall_settled_by_whole_po_receipt(database):
    """Receiving a whole PO settles shortfalls exactly like booking its lines
    one at a time -- the two receive paths must not drift apart."""
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP", "a component")
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 4.0)
    db.create_supplier(1, "Acme")
    db.create_supplier_part(1, 1, "C-1", 2, pack_qty=1)
    db.create_po(1, 1)
    db.add_po_line(db.next_line_id(), 1, 1, 4, 2.0)

    build_id = db.next_build_id()
    db.create_build(build_id, 1, 1)
    db.produce_build(build_id, 1)  # no stock at all: all 4 go on credit
    with db.session() as s:
        debt_id = (
            s.scalars(
                select(StockItem).where(StockItem.part_id == 2, StockItem.count < 0)
            )
            .one()
            .id
        )
    db.book_po(1)  # the whole-PO path, not book_po_line
    with db.session() as s:
        assert s.get(StockItem, debt_id).count == 0  # settled
        settled = s.scalars(
            select(StockItem).where(
                StockItem.consumed_by_build_id == build_id,
                StockItem.po_id.is_not(None),
            )
        ).one()
        assert (settled.count, settled.price_basis) == (4, "po")
        assert db.build_unit_cost(s, build_id) == (8.0, True)


def test_stocktake_up_and_down(database):
    """Counting more adds stock; counting less comes off one item, splitting off
    a consumed row so what left stays traceable."""
    db.create_part(1, "BOLT", "a bolt")
    db.stocktake(1, 10, "Lost")
    with db.session() as s:
        assert db.on_hand(s, 1) == 10
        added = s.scalars(select(StockItem).where(StockItem.part_id == 1)).one()
        assert added.stocktake_reason == "Lost"
        assert added.stocktake_at is not None

    # a second row, so "oldest by default" has something to choose between
    db.create_item(db.next_item_id(), 1)
    db.set_count(2, 5)
    db.stocktake(1, 12, "Damaged")  # 15 -> 12: 3 off the oldest item
    with db.session() as s:
        assert db.on_hand(s, 1) == 12
        assert s.get(StockItem, 1).count == 7  # taken off the oldest
        assert s.get(StockItem, 2).count == 5  # untouched
        gone = s.scalars(
            select(StockItem).where(
                StockItem.part_id == 1, StockItem.status == STOCK_CONSUMED
            )
        ).one()
        assert (gone.count, gone.stocktake_reason) == (3, "Damaged")

    # naming an item takes it off that one instead
    db.stocktake(1, 10, "Damaged", item_id=2)
    with db.session() as s:
        assert s.get(StockItem, 1).count == 7  # still untouched
        assert s.get(StockItem, 2).count == 3

    # automatic (no item named) spills onto the next item once one runs dry
    db.stocktake(1, 1, "Lost")  # 10 -> 1: drains item 1's 7, then 2 off item 2
    with db.session() as s:
        assert db.on_hand(s, 1) == 1
        assert s.get(StockItem, 1).count == 0
        assert s.get(StockItem, 2).count == 1


def test_stocktake_rejects_no_reason_and_going_negative(database):
    db.create_part(1, "BOLT", "a bolt")
    db.stocktake(1, 5, "Lost")
    with pytest.raises(db.InventoryError):
        db.stocktake(1, 2, "")  # every stocktake needs a reason
    with pytest.raises(db.InventoryError):
        db.stocktake(1, -1, "Lost")  # below zero is add_negative_stock's job
    with pytest.raises(db.InventoryError):
        db.stocktake(1, 1, "Damaged", item_id=99)  # not this part's stock


def test_stocktake_floor_is_what_the_part_already_owes(database):
    """A part already owing stock counts below zero. The stocktake may empty the
    shelf down to that debt, but never deepen it -- that needs the negative
    stock item button."""
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP", "a component")
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 1.0)
    build_id = db.next_build_id()
    db.create_build(build_id, 1, 1)
    db.stocktake(2, 10, "Found")
    db.add_negative_stock(2, 4, build_id)  # now 10 on the shelf, 4 owed
    with db.session() as s:
        assert db.on_hand(s, 2) == 6
        assert db.owed(s, 2) == -4

    with pytest.raises(db.InventoryError):
        db.stocktake(2, -5, "Lost")  # deeper than the existing debt
    db.stocktake(2, -4, "Lost")  # empties the shelf, debt untouched
    with db.session() as s:
        assert db.on_hand(s, 2) == -4
        assert db.owed(s, 2) == -4  # no new debt row was created
        shelf = [
            i
            for i in s.scalars(
                select(StockItem).where(
                    StockItem.part_id == 2, StockItem.status == STOCK_AVAILABLE
                )
            )
            if i.count > 0
        ]
        assert shelf == []


def test_negative_stocktake_settled_by_purchase(database):
    """Stock a build used but nobody booked. The debt must take the same shape
    produce_build's shortfall does -- adjacent consumed/debt rows -- or a later
    receipt would not settle it."""
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP", "a component")
    db.set_part_assembly(1, True)
    db.add_bomline(db.next_bomline_id(), 1, 2, 1.0)
    db.create_supplier(1, "Acme")
    db.create_supplier_part(1, 1, "C-1", 2, pack_qty=1)
    db.create_po(1, 1)
    line_id = db.next_line_id()
    db.add_po_line(line_id, 1, 1, 10, 3.0)  # comp estimate becomes 3.00
    build_id = db.next_build_id()
    db.create_build(build_id, 1, 1)

    db.add_negative_stock(2, 3, build_id)
    with db.session() as s:
        assert db.on_hand(s, 2) == -3
        debt = s.scalars(
            select(StockItem).where(StockItem.part_id == 2, StockItem.count < 0)
        ).one()
        assert debt.count == -3
        assert debt.consumed_by_build_id == build_id
        # the placeholder _settle_stock_debt pairs by `debt.id - 1`
        placeholder = s.get(StockItem, debt.id - 1)
        assert placeholder.count == 3
        assert placeholder.status == STOCK_CONSUMED
        assert placeholder.consumed_by_build_id == build_id

    # the missing parts arrive: the debt clears and is repriced to what was paid
    db.edit_po_line(line_id, price=2.0)
    item_id = db.next_item_id()
    db.book_po_line(line_id, item_id, 10)
    with db.session() as s:
        assert s.get(StockItem, debt.id).count == 0
        assert s.get(StockItem, item_id).count == 7  # 3 went straight to the build
        settled = s.scalars(
            select(StockItem).where(
                StockItem.consumed_by_build_id == build_id,
                StockItem.po_id.is_not(None),
            )
        ).one()
        assert (settled.count, settled.price_basis) == (3, "po")
        assert settled.unit_price == 2.0


def test_virtual_part(database):
    db.create_part(1, "ASM", "an assembly")
    db.create_part(2, "COMP-A", "component a")
    db.create_part(3, "LABOUR", "hourly labour", virtual=True)  # unlimited stock
    db.set_part_assembly(1, True)

    # assembly and virtual are mutually exclusive, both ways
    with pytest.raises(db.InventoryError):
        db.set_part_virtual(1, True)
    with pytest.raises(db.InventoryError):
        db.set_part_assembly(3, True)

    db.set_part_price(3, 10.0)  # 10.00/h
    db.add_bomline(db.next_bomline_id(), 1, 2, 1.0)
    db.add_bomline(db.next_bomline_id(), 1, 3, 5.0)  # 5h labour, never stocked
    db.create_item(1, 2)
    db.set_count(1, 4)

    build_id = db.next_build_id()
    db.create_build(build_id, 1, 2)  # 2x assembly: needs 2 comp-a; labour is virtual
    db.produce_build(build_id, 2)  # no shortage despite no labour stock
    with db.session() as s:
        assert s.get(StockItem, 1).count == 2  # comp-a consumed, stock untouched
        consumed = s.scalars(
            select(StockItem).where(StockItem.status == "Consumed by build order")
        ).all()
        # labour IS recorded as consumed (so the user sees it and it costs the
        # build), but no Available labour stock is ever drawn down
        assert {c.part_id for c in consumed} == {2, 3}
        labour = next(c for c in consumed if c.part_id == 3)
        assert labour.count == 10.0  # 5h x 2 units
        assert labour.unit_price == 10.0
        assert labour.price_basis == "virtual"  # exact, not an estimate
        assert not s.scalars(
            select(StockItem).where(
                StockItem.part_id == 3, StockItem.status == "Available"
            )
        ).all()
        assert db.produced_qty(s, build_id) == 2
        # the labour is in the build's cost: 2x comp-a + 10h @ 10.00 over 2 units
        unit, exact = db.build_unit_cost(s, build_id)
        # comp-a has no price at all, so the cost is a floor -- but the labour
        # is counted in full: 10h x 10.00 = 100.00 over 2 units
        assert unit == 50.0
        assert not exact

    # regression: a partly-produced build costs its output over what it MADE,
    # not over what it ordered. Producing 1 of a 50-unit order must not price
    # that unit at a fiftieth of the stock it actually consumed.
    db.create_part(6, "PARTIAL", "partly built assembly")
    db.set_part_assembly(6, True)
    db.add_bomline(db.next_bomline_id(), 6, 2, 1.0)
    db.add_bomline(db.next_bomline_id(), 6, 3, 2.0)  # 2h labour @ 10.00
    part_build = db.next_build_id()
    db.create_build(part_build, 6, 50)
    db.edit_build(part_build, 50, status="Production")
    db.produce_build(part_build, 1)  # 1 of 50
    with db.session() as s:
        unit, _exact = db.build_unit_cost(s, part_build)
        assert db.produced_qty(s, part_build) == 1
        assert unit == 20.0  # 2h x 10.00 over the ONE unit made, not over 50
        made = s.scalars(
            select(StockItem).where(StockItem.build_id == part_build)
        ).one()
        assert made.unit_price == 20.0

    # a build that has produced nothing has consumed no labour either: virtual
    # consumption follows units MADE, never units ordered
    idle_id = db.next_build_id()
    db.create_build(idle_id, 1, 50)
    with db.session() as s:
        assert not s.scalars(
            select(StockItem).where(StockItem.consumed_by_build_id == idle_id)
        ).all()
        assert db.build_unit_cost(s, idle_id) == (None, False)

    # the build snapshotted the labour rate, so raising it later does not
    # re-cost a build that was already priced at the old rate
    db.set_part_price(3, 40.0)
    snap_id = db.next_build_id()
    db.create_build(snap_id, 1, 1)
    db.set_part_price(3, 90.0)
    with db.session() as s:
        rates = {
            c: price
            for _l, c, _s, _d, _q, _v, price, _p in db.build_lines_for(s, snap_id)
        }
        assert rates[3] == 40.0  # snapshot price, not the new 90
        # a legacy row without a snapshot price falls back to the part's current one
        line = s.scalars(
            select(BuildLine).where(
                BuildLine.build_id == snap_id, BuildLine.component_part_id == 3
            )
        ).one()
        line.unit_price = None
        s.commit()
        rates = {
            c: price
            for _l, c, _s, _d, _q, _v, price, _p in db.build_lines_for(s, snap_id)
        }
        assert rates[3] == 90.0


def test_settings(database):
    assert db.get_setting("gui.title") == "Stronghold"
    db.set_setting("gui.title", "My Stock")
    assert db.get_setting("gui.title") == "My Stock"
    with pytest.raises(db.InventoryError):
        db.set_setting("nope", "x")


def test_export_restore_roundtrip(database, tmp_path):
    db.create_part(1, "BOLT-M3", "M3 bolt")
    db.create_item(1, 1)
    db.set_count(1, 4)
    sql = database.read_text(encoding="utf-8")
    # restore into a raw empty db: the export's CREATE TABLEs must stand alone,
    # and the INSERTs must survive omitting NULL/derived columns
    fresh = tmp_path / "restored.db"
    with sqlite3.connect(fresh) as conn:
        conn.executescript(sql)
    with sqlite3.connect(fresh) as conn:
        assert conn.execute("SELECT count FROM stock_items").fetchone()[0] == 4


def test_startup_rebuilds_db_from_sql(database):
    """The .sql is the truth: dropping the .db and re-initialising restores
    everything, which is what a git checkout + restart does."""
    db.create_part(1, "BOLT-M3", "M3 bolt")
    db.create_supplier(1, "acme")
    sp_id = db.next_supplier_part_id()
    db.create_supplier_part(sp_id, 1, "SP-1", 1, pack_qty=10)
    po_id = db.next_po_id()
    db.create_po(po_id, 1)
    line_id = db.next_line_id()
    db.add_po_line(line_id, po_id, sp_id, quantity=2, price=5.0)
    item_id = db.next_item_id()
    db.book_po_line(line_id, item_id, 2)
    with db.session() as s:
        priced = s.get(StockItem, item_id)
        price, basis = priced.unit_price, priced.price_basis
    assert price  # the cache we deliberately stop exporting

    # the working .db is scratch: deleting it loses nothing, init rebuilds it
    working = db._db_path
    assert working.exists()
    working.unlink()
    db.init(database)
    assert db._db_path.exists()

    with db.session() as s:
        assert s.get(Part, 1).sku == "BOLT-M3"
        assert s.get(StockItem, 1).count == 20  # 2 packs of 10
        # price caches are left out of the .sql; refresh_all_prices rebuilds them
        db.refresh_all_prices()
    with db.session() as s:
        item = s.get(StockItem, 1)
        assert item.unit_price == price
        assert item.price_basis == basis


def test_working_db_lives_outside_the_data_folder(tmp_path):
    """The .db is an implementation detail: it belongs in a temp dir, and two
    data files must never share one."""
    a, b = tmp_path / "a.sql", tmp_path / "nested" / "b.sql"
    b.parent.mkdir()
    db.init(a)
    db.create_part(1, "A", "from a")
    path_a = db._db_path
    db.init(b)
    db.create_part(1, "B", "from b")

    assert path_a != db._db_path
    assert tmp_path not in path_a.parents  # not next to the data
    assert list(tmp_path.glob("*.db")) == []
    db.init(a)
    with db.session() as s:
        assert s.get(Part, 1).description == "from a"


def test_non_purchasable_part(database):
    db.create_part(1, "MADE", "made in house")
    db.create_supplier(1, "acme")
    db.set_part_purchasable(1, False)

    with pytest.raises(db.InventoryError):
        db.create_supplier_part(db.next_supplier_part_id(), 1, "SP-1", 1)

    # a part a supplier already sells cannot be marked non-purchasable
    db.set_part_purchasable(1, True)
    sp_id = db.next_supplier_part_id()
    db.create_supplier_part(sp_id, 1, "SP-1", 1)
    with pytest.raises(db.InventoryError):
        db.set_part_purchasable(1, False)
