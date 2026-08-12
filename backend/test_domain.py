"""Integration tests driving the real flows: parts -> stock -> purchasing ->
booking -> export -> restore."""

import json
import sqlite3
from datetime import date

import db
import pytest
from models import Activity, Booking, BuildLine, Part, POLine, PurchaseOrder, StockItem
from sqlalchemy import select


def test_parts_and_stock_flow(database):
    part_id = db.next_part_id()
    db.create_part(part_id, "BOLT-M3", "M3 bolt")
    db.edit_part(part_id, "M3 hex bolt")
    item_id = db.next_item_id()
    db.create_item(item_id, part_id)
    db.adjust_count(item_id, 5)
    db.adjust_count(item_id, -2)
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
    sql = database.with_suffix(".sql").read_text(encoding="utf-8")
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
    sql = database.with_suffix(".sql").read_text(encoding="utf-8")
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
    sql = database.with_suffix(".sql").read_text(encoding="utf-8")
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
    # It produces the full assembly count; comp-c stays at zero (never negative).
    db.produce_build(build_id, 1)
    with db.session() as s:
        assert s.get(StockItem, 4).count == 0  # short comp-c drained, not negative
        # only 3 comp-c was ever consumed despite the BOM asking for 4
        cc_consumed = s.scalars(
            select(StockItem).where(
                StockItem.status == "Consumed by build order",
                StockItem.part_id == 4,
            )
        ).all()
        assert sum(c.count for c in cc_consumed) == 3
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
    sql = database.with_suffix(".sql").read_text(encoding="utf-8")
    assert "INSERT INTO build_orders" in sql


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
    db.adjust_count(1, 4)
    sql = database.with_suffix(".sql").read_text(encoding="utf-8")
    # restore into a raw empty db: the export's CREATE TABLEs must stand alone
    fresh = tmp_path / "restored.db"
    with sqlite3.connect(fresh) as conn:
        conn.executescript(sql)
    db.init(fresh)  # re-point at the restored db
    with db.session() as s:
        assert s.get(StockItem, 1).count == 4
        assert s.get(Part, 1).sku == "BOLT-M3"


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
