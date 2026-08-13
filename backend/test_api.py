"""API-level integration tests: drive the JSON routes the frontend uses, on a
tmp database. Complements test_domain.py (which exercises db directly)."""

import db
import pytest
from models import StockItem


def test_parts_crud_and_bom(client):
    r = client.post("/api/parts", json={"sku": "ASM", "description": "assembly"})
    assert r.status_code == 201
    asm = r.json()["id"]
    comp = client.post("/api/parts", json={"sku": "C1", "description": "comp"}).json()[
        "id"
    ]

    # edit + toggle assembly via PATCH
    client.patch(f"/api/parts/{asm}", json={"description": "an assembly"})
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    assert client.get(f"/api/parts/{asm}").json()["assembly"] is True

    # bom add / edit / delete
    client.post(
        f"/api/parts/{asm}/bom", json={"component_part_id": comp, "quantity": 2}
    )
    bom = client.get(f"/api/parts/{asm}/bom").json()
    assert len(bom) == 1 and bom[0]["component_sku"] == "C1"
    line_id = bom[0]["id"]
    client.patch(f"/api/bom/{line_id}", json={"quantity": 5})
    assert client.get(f"/api/parts/{asm}/bom").json()[0]["quantity"] == 5
    client.delete(f"/api/bom/{line_id}")
    assert client.get(f"/api/parts/{asm}/bom").json() == []

    # a domain rejection becomes a 400 with the message
    bad = client.post(
        f"/api/parts/{asm}/bom", json={"component_part_id": asm, "quantity": 1}
    )
    assert bad.status_code == 400
    assert "itself" in bad.json()["detail"]


def test_stock_flow(client):
    pid = client.post("/api/parts", json={"sku": "BOLT", "description": "bolt"}).json()[
        "id"
    ]
    item = client.post("/api/stock", json={"part_id": pid}).json()
    assert item["count"] == 0 and item["sku"] == "BOLT"
    client.patch(f"/api/stock/{item['id']}", json={"count": 7})
    assert client.get(f"/api/stock/{item['id']}").json()["count"] == 7
    assert item["status"] == "Available"
    client.patch(f"/api/stock/{item['id']}", json={"status": "Consumed by build order"})
    assert (
        client.get(f"/api/stock/{item['id']}").json()["status"]
        == "Consumed by build order"
    )


def test_part_stock_log(client):
    """The log classifies each stock row as the event that made it, oldest
    first. A consumed row carries both the PO it was bought on and the build
    that ate it -- it must be dated by the build, or the log claims the stock
    was used before it was ordered."""
    asm = client.post("/api/parts", json={"sku": "ASM", "description": "asm"}).json()[
        "id"
    ]
    comp = client.post("/api/parts", json={"sku": "C1", "description": "comp"}).json()[
        "id"
    ]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm}/bom", json={"component_part_id": comp, "quantity": 2}
    )
    sup = client.post("/api/suppliers", json={"name": "Acme"}).json()["id"]
    sp = client.post(
        "/api/supplier-parts",
        json={
            "description": "d",
            "supplier_id": sup,
            "sku": "A-1",
            "part_id": comp,
            "pack_qty": 1,
        },
    ).json()["id"]
    # the PO is ordered well before the build that eats its stock
    po = client.post(
        "/api/purchase-orders",
        json={"description": "d", "supplier_id": sup, "start_date": "2024-01-01"},
    ).json()["id"]
    line = client.post(
        f"/api/purchase-orders/{po}/lines",
        json={"supplier_part_id": sp, "quantity": 10, "price": 1.0},
    ).json()[0]["id"]
    client.post(f"/api/po-lines/{line}/book", json={"quantity": 10})

    build = client.post(
        "/api/build-orders",
        json={
            "description": "d",
            "part_id": asm,
            "quantity": 3,
            "start_date": "2024-06-01",
        },
    ).json()["id"]
    client.patch(f"/api/build-orders/{build}", json={"status": "Production"})
    client.post(f"/api/build-orders/{build}/produce", json={"quantity": 3})
    client.post(
        "/api/stock/stocktake",
        json={"part_id": comp, "count": 1, "reason": "Damaged"},
    )

    log = client.get(f"/api/parts/{comp}/stock-log").json()
    kinds = [e["kind"] for e in log]
    assert kinds == ["received", "consumed", "stocktake"]

    received, consumed, stocktake = log
    # the consumption is dated by the build, NOT by the PO that supplied it
    assert received["at"].startswith("2024-01-01")
    assert consumed["at"].startswith("2024-06-01")
    assert consumed["order_label"] == received["order_label"].replace("PO", "BO") or (
        consumed["order_url"] == f"/build-orders/{build}"
    )
    # order dates are approximate; a stocktake carries the real moment
    assert received["at_approx"] and consumed["at_approx"]
    assert stocktake["at_approx"] is False
    assert stocktake["reason"] == "Damaged"
    # signed like a ledger: in positive, out negative
    assert consumed["quantity"] == -6  # 2 per assembly x 3
    assert stocktake["quantity"] == -3  # 4 left on the shelf, counted 1
    # the receipt booked 10 but later events ate into that row, so it reads as
    # the 1 remaining and says so rather than claiming 10 arrived
    assert received["quantity"] == 1
    assert received["drawn_down"] is True
    assert consumed["drawn_down"] is False

    # the assembly's own log shows what the build produced
    assert [e["kind"] for e in client.get(f"/api/parts/{asm}/stock-log").json()] == [
        "produced"
    ]

    # add_negative_stock stamps stocktake_at too, but a build owing the stock is
    # the real event -- the pair must read as consumed + debt, not as stocktakes
    client.post(
        "/api/stock/negative",
        json={"part_id": comp, "quantity": 2, "build_id": build},
    )
    after = client.get(f"/api/parts/{comp}/stock-log").json()
    assert [e["kind"] for e in after if e["item_id"] > stocktake["item_id"]] == [
        "consumed",
        "debt",
    ]
    debt = next(e for e in after if e["kind"] == "debt")
    assert debt["quantity"] == -2 and debt["order_url"] == f"/build-orders/{build}"

    # the consumed row here was split off a surviving receipt, so that receipt
    # is reported once -- not again by the slice that carries the same po_id
    assert [e["kind"] for e in after].count("received") == 1


def test_stock_log_reports_imported_receipts(client):
    """Imported consumed stock carries a po_id but has no surviving row to
    report that receipt (InvenTree writes the consumed row directly). Such a row
    is two events: it arrived on that PO, and a build ate it. Without this the
    receipt is invisible -- 2075 of them on the real dataset."""
    part = client.post("/api/parts", json={"sku": "C", "description": "comp"}).json()[
        "id"
    ]
    sup = client.post("/api/suppliers", json={"name": "Acme"}).json()["id"]
    client.post(
        "/api/supplier-parts",
        json={
            "description": "d",
            "supplier_id": sup,
            "sku": "A-1",
            "part_id": part,
            "pack_qty": 1,
        },
    )
    po = client.post(
        "/api/purchase-orders",
        json={"description": "d", "supplier_id": sup, "start_date": "2024-01-01"},
    ).json()["id"]
    asm = client.post("/api/parts", json={"sku": "A", "description": "asm"}).json()[
        "id"
    ]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    build = client.post(
        "/api/build-orders",
        json={
            "description": "d",
            "part_id": asm,
            "quantity": 1,
            "start_date": "2024-06-01",
        },
    ).json()["id"]

    # the shape the InvenTree import writes: consumed, stamped with its PO,
    # with no Available row of its own
    with db.session() as s:
        s.add(
            StockItem(
                id=db.next_item_id(),
                part_id=part,
                count=7,
                po_id=po,
                consumed_by_build_id=build,
                status="Consumed by build order",
            )
        )
        s.commit()

    log = client.get(f"/api/parts/{part}/stock-log").json()
    assert [e["kind"] for e in log] == ["received", "consumed"]
    assert log[0]["at"].startswith("2024-01-01") and log[0]["quantity"] == 7
    assert log[1]["at"].startswith("2024-06-01") and log[1]["quantity"] == -7


def test_stock_log_reports_imported_production(client):
    """The same for an assembly: imported stock that one build made and another
    ate carries both build ids, and nothing else records the production. The
    row is two events -- built, then consumed -- so an assembly's log is not
    only the builds that ate it."""
    asm = client.post("/api/parts", json={"sku": "A", "description": "asm"}).json()[
        "id"
    ]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    made = client.post(
        "/api/build-orders",
        json={
            "description": "d",
            "part_id": asm,
            "quantity": 5,
            "start_date": "2024-01-01",
        },
    ).json()["id"]
    bigger = client.post("/api/parts", json={"sku": "B", "description": "big"}).json()[
        "id"
    ]
    client.patch(f"/api/parts/{bigger}", json={"assembly": True})
    ate = client.post(
        "/api/build-orders",
        json={
            "description": "d",
            "part_id": bigger,
            "quantity": 1,
            "start_date": "2024-06-01",
        },
    ).json()["id"]

    with db.session() as s:
        s.add(
            StockItem(
                id=db.next_item_id(),
                part_id=asm,
                count=5,
                build_id=made,
                consumed_by_build_id=ate,
                status="Consumed by build order",
            )
        )
        s.commit()

    log = client.get(f"/api/parts/{asm}/stock-log").json()
    assert [e["kind"] for e in log] == ["produced", "consumed"]
    assert log[0]["at"].startswith("2024-01-01") and log[0]["quantity"] == 5
    assert log[0]["order_url"] == f"/build-orders/{made}"
    assert log[1]["at"].startswith("2024-06-01") and log[1]["quantity"] == -5
    assert log[1]["order_url"] == f"/build-orders/{ate}"


def test_purchasing_and_booking(client):
    pid = client.post("/api/parts", json={"sku": "BOLT", "description": "bolt"}).json()[
        "id"
    ]
    sup_id = client.post("/api/suppliers", json={"name": "Acme"}).json()["id"]
    sp_id = client.post(
        "/api/supplier-parts",
        json={"supplier_id": sup_id, "sku": "A-1", "part_id": pid, "pack_qty": 10},
    ).json()["id"]
    po = client.post(
        "/api/purchase-orders",
        json={"description": "d", "supplier_id": sup_id, "end_date": "2026-03-01"},
    ).json()
    po_id = po["id"]
    assert po["end_date"] == "2026-03-01"  # dates round-trip as ISO strings
    assert po["status"] == "Pending"  # new POs default to a valid status
    # a date can be cleared to null
    assert client.patch(
        f"/api/purchase-orders/{po_id}", json={"end_date": None}
    ).is_success
    assert client.get(f"/api/purchase-orders/{po_id}").json()["end_date"] is None
    # only InvenTree PurchaseOrderStatus labels are accepted
    assert (
        client.patch(
            f"/api/purchase-orders/{po_id}", json={"status": "Bogus"}
        ).status_code
        == 422
    )
    assert client.patch(
        f"/api/purchase-orders/{po_id}", json={"status": "Placed"}
    ).is_success
    lines = client.post(
        f"/api/purchase-orders/{po_id}/lines",
        json={"supplier_part_id": sp_id, "quantity": 20, "price": 0.05},
    ).json()
    line_id = lines[0]["id"]
    # empty / non-positive receive qty is rejected before it reaches the domain
    assert (
        client.post(f"/api/po-lines/{line_id}/book", json={"quantity": ""}).status_code
        == 422
    )
    assert (
        client.post(f"/api/po-lines/{line_id}/book", json={"quantity": 0}).status_code
        == 422
    )
    # receive it -> creates stock (count * pack_qty), records received, links PO
    assert client.post(
        f"/api/po-lines/{line_id}/book", json={"quantity": 20}
    ).is_success
    after = client.get(f"/api/purchase-orders/{po_id}/lines").json()
    assert after[0]["received"] == 20
    # fully receiving every line flips the PO to Complete
    assert client.get(f"/api/purchase-orders/{po_id}").json()["status"] == "Complete"
    # received line cannot be removed -> 400
    assert client.delete(f"/api/po-lines/{line_id}").status_code == 400
    stock = client.get("/api/stock").json()
    assert any(
        s["count"] == 200 and s["part_id"] == pid and s["po_id"] == po_id for s in stock
    )
    # cross-link: the part reaches its PO through its supplier part
    part_pos = client.get(f"/api/parts/{pid}/purchase-orders").json()
    assert [p["id"] for p in part_pos] == [po_id]


def test_receive_all_and_sku_edit(client):
    pid = client.post("/api/parts", json={"sku": "OLD", "description": "p"}).json()[
        "id"
    ]
    # part sku is editable
    client.patch(f"/api/parts/{pid}", json={"sku": "NEW"})
    assert client.get(f"/api/parts/{pid}").json()["sku"] == "NEW"

    sup_id = client.post("/api/suppliers", json={"name": "S"}).json()["id"]
    sp_id = client.post(
        "/api/supplier-parts",
        json={"supplier_id": sup_id, "sku": "SP", "part_id": pid, "pack_qty": 2},
    ).json()["id"]
    po_id = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup_id}
    ).json()["id"]
    for qty in (5, 3):
        client.post(
            f"/api/purchase-orders/{po_id}/lines",
            json={"supplier_part_id": sp_id, "quantity": qty, "price": 1.0},
        )
    # receive everything in one call: both lines fully received, PO -> Complete
    assert client.post(f"/api/purchase-orders/{po_id}/receive-all").is_success
    lines = client.get(f"/api/purchase-orders/{po_id}/lines").json()
    assert all(line["received"] == line["quantity"] for line in lines)
    assert client.get(f"/api/purchase-orders/{po_id}").json()["status"] == "Complete"
    # stock created = outstanding * pack_qty for each line (5*2 and 3*2)
    counts = sorted(
        s["count"] for s in client.get("/api/stock").json() if s["po_id"] == po_id
    )
    assert counts == [6, 10]
    # receive-all again is a no-op (nothing outstanding), no extra stock
    client.post(f"/api/purchase-orders/{po_id}/receive-all")
    assert sorted(
        s["count"] for s in client.get("/api/stock").json() if s["po_id"] == po_id
    ) == [6, 10]


def test_part_used_in(client):
    asm = client.post("/api/parts", json={"sku": "ASM", "description": "a"}).json()[
        "id"
    ]
    comp = client.post("/api/parts", json={"sku": "C", "description": "c"}).json()["id"]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm}/bom", json={"component_part_id": comp, "quantity": 3}
    )
    used = client.get(f"/api/parts/{comp}/used-in").json()
    assert len(used) == 1
    assert used[0]["parent_part_id"] == asm and used[0]["quantity"] == 3


def test_supplier_part_edit(client):
    pid = client.post("/api/parts", json={"sku": "P", "description": "p"}).json()["id"]
    sup_id = client.post("/api/suppliers", json={"name": "S co"}).json()["id"]
    sp_id = client.post(
        "/api/supplier-parts",
        json={"supplier_id": sup_id, "sku": "X", "part_id": pid},
    ).json()["id"]
    client.patch(f"/api/supplier-parts/{sp_id}", json={"ean": "999", "pack_qty": 4})
    got = client.get(f"/api/supplier-parts/{sp_id}").json()
    assert got["ean"] == "999" and got["pack_qty"] == 4
    assert got["part_description"] == "p"  # linked part's description is joined in
    # non-positive pack_qty rejected
    assert (
        client.patch(f"/api/supplier-parts/{sp_id}", json={"pack_qty": 0}).status_code
        == 422
    )
    client.patch(f"/api/supplier-parts/{sp_id}", json={"active": False})
    assert client.get(f"/api/supplier-parts/{sp_id}").json()["active"] is False


def test_build_flow(client):
    asm = client.post("/api/parts", json={"sku": "ASM", "description": "a"}).json()[
        "id"
    ]
    comp = client.post("/api/parts", json={"sku": "C", "description": "c"}).json()["id"]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm}/bom", json={"component_part_id": comp, "quantity": 2}
    )
    # stock the component: 10 units
    item = client.post("/api/stock", json={"part_id": comp}).json()
    client.patch(f"/api/stock/{item['id']}", json={"count": 10})

    # building a non-assembly part is rejected
    assert (
        client.post(
            "/api/build-orders",
            json={"description": "d", "part_id": comp, "quantity": 1},
        ).status_code
        == 400
    )
    # invalid status is rejected by the schema
    assert (
        client.post(
            "/api/build-orders",
            json={"description": "d", "part_id": asm, "quantity": 3, "status": "Bogus"},
        ).status_code
        == 422
    )

    build = client.post(
        "/api/build-orders", json={"description": "d", "part_id": asm, "quantity": 3}
    ).json()
    bid = build["id"]
    assert build["status"] == "Draft"
    assert build["produced"] == 0
    # reverse lookup: the build shows up under its assembly part
    assert [b["id"] for b in client.get(f"/api/parts/{asm}/builds").json()] == [bid]
    # and under the component it consumes, needing 2 per unit but taking none yet
    consumers = client.get(f"/api/parts/{comp}/consumed-by").json()
    assert [(b["id"], b["required"], b["consumed"]) for b in consumers] == [
        (bid, 6.0, 0.0)
    ]

    # produce 1 of 3: consumes 2 comp; not yet Complete
    r = client.post(f"/api/build-orders/{bid}/produce", json={"quantity": 1})
    assert r.is_success and r.json()["produced"] == 1
    # the consumed figure now tracks the stock actually taken
    assert client.get(f"/api/parts/{comp}/consumed-by").json()[0]["consumed"] == 2.0
    assert r.json()["status"] != "Complete"
    # producing more than remaining (2 left) is rejected
    assert (
        client.post(
            f"/api/build-orders/{bid}/produce", json={"quantity": 3}
        ).status_code
        == 400
    )
    # status transition rules once production has started (produced 1 of 3):
    # cannot complete before fully produced, cannot revert to a pre-production
    # status; only Production remains
    assert (
        client.patch(
            f"/api/build-orders/{bid}", json={"status": "Complete"}
        ).status_code
        == 400
    )
    assert (
        client.patch(f"/api/build-orders/{bid}", json={"status": "Pending"}).status_code
        == 400
    )
    assert (
        client.patch(f"/api/build-orders/{bid}", json={"status": "Draft"}).status_code
        == 400
    )
    assert client.patch(
        f"/api/build-orders/{bid}", json={"status": "Production"}
    ).is_success

    # produce the last 2: consumes 4 comp, flips to Complete
    assert client.post(
        f"/api/build-orders/{bid}/produce", json={"quantity": 2}
    ).is_success
    assert client.get(f"/api/build-orders/{bid}").json()["status"] == "Complete"
    stock = client.get("/api/stock").json()
    # source component reduced 10 -> 4 (6 consumed), consumed stock preserved
    assert any(s["id"] == item["id"] and s["count"] == 4 for s in stock)
    consumed = [
        s
        for s in stock
        if s["status"] == "Consumed by build order" and s["consumed_by_build_id"] == bid
    ]
    assert sum(s["count"] for s in consumed) == 6
    produced = [s for s in stock if s["part_id"] == asm and s["status"] == "Available"]
    assert sum(s["count"] for s in produced) == 3

    # the produces show up in the activity log with structured refs, newest first
    log = client.get("/api/activity").json()
    produces = [a for a in log if a["action"] == "produce_build"]
    assert produces and "produced" in produces[0]["message"]
    types = {r["type"] for r in produces[0]["refs"]}
    assert {"build", "stock"} <= types
    assert all(isinstance(r["id"], int) for r in produces[0]["refs"])

    # shortage does not block: components are exhausted, but producing anyway
    # succeeds -- it produces the full assembly count and consumes what exists
    big = client.post(
        "/api/build-orders", json={"description": "d", "part_id": asm, "quantity": 5}
    ).json()["id"]
    r = client.post(f"/api/build-orders/{big}/produce", json={"quantity": 5})
    assert r.is_success and r.json()["produced"] == 5
    assert r.json()["status"] == "Complete"


def test_settings(client):
    assert {"key": "gui.title", "value": "Stronghold"} in client.get(
        "/api/settings"
    ).json()
    client.put("/api/settings/gui.title", json={"value": "My Stock"})
    assert client.get("/api/settings").json()[0]["value"] == "My Stock"
    assert client.put("/api/settings/nope", json={"value": "x"}).status_code == 400


def test_stocktake_reasons(client):
    """Served split by direction, parsed from the comma-separated setting."""
    reasons = client.get("/api/settings/stocktake-reasons").json()
    assert "Damaged" in reasons["subtract"]
    assert "Refurbished/repaired" in reasons["add"]
    assert "Unknown" in reasons["add"] and "Unknown" in reasons["subtract"]

    client.put("/api/settings/stocktake.add_reasons", json={"value": "Found, Given"})
    assert client.get("/api/settings/stocktake-reasons").json()["add"] == [
        "Found",
        "Given",
    ]


def test_search(client):
    assert client.get("/api/search", params={"q": ""}).json() == []

    active = client.post(
        "/api/parts", json={"sku": "SRCH-1", "description": "widget"}
    ).json()
    inactive = client.post(
        "/api/parts", json={"sku": "SRCH-2", "description": "gadget"}
    ).json()
    client.patch(f"/api/parts/{inactive['id']}", json={"active": False})
    sup = client.post("/api/suppliers", json={"name": "SrchCo"}).json()
    po = client.post(
        "/api/purchase-orders",
        json={"description": "d", "supplier_id": sup["id"], "reference": "SRCH-PO-1"},
    ).json()

    hits = client.get("/api/search", params={"q": "srch"}).json()
    types = {h["type"] for h in hits}
    assert types == {"part", "supplier", "purchase_order"}
    ids = {(h["type"], h["id"]) for h in hits}
    assert ("part", active["id"]) in ids
    assert ("part", inactive["id"]) not in ids
    assert ("supplier", sup["id"]) in ids
    assert ("purchase_order", po["id"]) in ids

    hits_all = client.get(
        "/api/search", params={"q": "srch", "include_inactive": True}
    ).json()
    assert ("part", inactive["id"]) in {(h["type"], h["id"]) for h in hits_all}

    # word order does not matter: each word must match, not the phrase as-is
    din = client.post(
        "/api/parts", json={"sku": "DIN933", "description": "DIN 933 bolt"}
    ).json()
    reordered = client.get("/api/search", params={"q": "933 DIN"}).json()
    assert ("part", din["id"]) in {(h["type"], h["id"]) for h in reordered}


def test_stock_value_report(client):
    part = client.post(
        "/api/parts", json={"sku": "VAL-1", "description": "valued"}
    ).json()
    orphan = client.post(
        "/api/parts", json={"sku": "VAL-2", "description": "unpriced"}
    ).json()
    sup = client.post("/api/suppliers", json={"name": "ValCo"}).json()
    sp = client.post(
        "/api/supplier-parts",
        json={
            "supplier_id": sup["id"],
            "sku": "SP-VAL",
            "part_id": part["id"],
            "description": "",
            "ean": "",
            "hyperlink": "",
            "pack_qty": 10,
        },
    ).json()
    po = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
    ).json()
    line = client.post(
        f"/api/purchase-orders/{po['id']}/lines",
        json={"supplier_part_id": sp["id"], "quantity": 2, "price": 50.0},
    ).json()[0]
    # booked stock: 2 packs of 10 = 20 items at 50/pack -> 5.0 each
    client.post(f"/api/po-lines/{line['id']}/book", json={"quantity": 2})
    # manual stock of the same part -> estimated from that PO line
    guessed = client.post("/api/stock", json={"part_id": part["id"]}).json()
    client.patch(f"/api/stock/{guessed['id']}", json={"count": 4})
    # stock of a part no PO ever priced -> no value
    client.post("/api/stock", json={"part_id": orphan["id"]})
    # migrated stock: linked to a PO but with no booking naming the line.
    # Still priced from that PO, not guessed.
    migrated = client.post("/api/stock", json={"part_id": part["id"]}).json()
    client.patch(f"/api/stock/{migrated['id']}", json={"count": 3})
    with db.session() as s:
        s.get(StockItem, migrated["id"]).po_id = po["id"]
        s.commit()
    # written behind the API, so no write hook repriced it: this is exactly what
    # the startup/backfill refresh is for
    client.post("/api/parts/refresh-prices")

    report = client.get("/api/reports/stock-value").json()
    rows = {r["item_id"]: r for r in report["rows"]}
    booked = next(r for r in report["rows"] if r["count"] == 20)
    assert (booked["basis"], booked["unit_price"], booked["value"]) == (
        "po",
        5.0,
        100.0,
    )
    assert booked["basis_po_id"] == po["id"]
    # po_id but no booking: still the PO's own price, not an estimate
    assert rows[migrated["id"]]["basis"] == "po"
    assert rows[migrated["id"]]["value"] == 15.0
    assert rows[guessed["id"]]["basis"] == "estimate"
    assert rows[guessed["id"]]["value"] == 20.0
    unpriced = next(r for r in report["rows"] if r["basis"] == "none")
    assert unpriced["unit_price"] is None and unpriced["value"] is None
    assert report["total"] == 135.0
    assert report["unpriced"] == 1

    # a build short of a component carries the shortfall as negative stock,
    # which is deducted from the total
    asm = client.post(
        "/api/parts", json={"sku": "VAL-ASM", "description": "assembly"}
    ).json()
    client.patch(f"/api/parts/{asm['id']}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": part["id"], "quantity": 30.0},
    )
    build = client.post(
        "/api/build-orders",
        json={"description": "d", "part_id": asm["id"], "quantity": 1},
    ).json()
    client.post(f"/api/build-orders/{build['id']}/produce", json={"quantity": 1})
    report = client.get("/api/reports/stock-value").json()
    debt = next(r for r in report["rows"] if r["count"] < 0)
    assert debt["count"] == -3.0  # 30 needed, 27 on the shelf
    assert (debt["basis"], debt["value"]) == ("estimate", -15.0)

    # a PO line with no price filled in: reported as such, not valued at 0.00
    free_part = client.post(
        "/api/parts", json={"sku": "VAL-3", "description": "free"}
    ).json()
    free_sp = client.post(
        "/api/supplier-parts",
        json={
            "supplier_id": sup["id"],
            "sku": "SP-FREE",
            "part_id": free_part["id"],
            "description": "",
            "ean": "",
            "hyperlink": "",
            "pack_qty": 1,
        },
    ).json()
    free_po = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
    ).json()
    free_line = client.post(
        f"/api/purchase-orders/{free_po['id']}/lines",
        json={"supplier_part_id": free_sp["id"], "quantity": 5},
    ).json()[0]
    assert free_line["price"] == 0.0
    client.post(f"/api/po-lines/{free_line['id']}/book", json={"quantity": 5})

    report = client.get("/api/reports/stock-value").json()
    no_price = next(r for r in report["rows"] if r["basis"] == "po_no_price")
    assert no_price["unit_price"] is None and no_price["value"] is None
    assert no_price["basis_po_id"] == free_po["id"]
    assert report["total"] == 135.0  # unchanged: a priceless line adds nothing
    assert report["unpriced"] == 2

    # once another order prices that part, the priceless stock is estimated
    # from it rather than left unpriced
    later_po = client.post(
        "/api/purchase-orders",
        json={"description": "d", "supplier_id": sup["id"], "reference": "PO-LATER"},
    ).json()
    client.patch(
        f"/api/purchase-orders/{later_po['id']}", json={"start_date": "2030-01-01"}
    )
    client.post(
        f"/api/purchase-orders/{later_po['id']}/lines",
        json={"supplier_part_id": free_sp["id"], "quantity": 1, "price": 7.0},
    )

    report = client.get("/api/reports/stock-value").json()
    was_priceless = next(r for r in report["rows"] if r["count"] == 5)
    assert was_priceless["basis"] == "estimate"
    assert was_priceless["unit_price"] == 7.0
    assert was_priceless["value"] == 35.0
    assert was_priceless["basis_po_id"] == later_po["id"]
    assert not [r for r in report["rows"] if r["basis"] == "po_no_price"]
    assert report["total"] == 170.0
    assert report["unpriced"] == 1

    # "latest" means the most recent order date, not the highest id: this PO is
    # created last but dated earlier, so it must not win the estimate
    older_po = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
    ).json()
    client.patch(
        f"/api/purchase-orders/{older_po['id']}", json={"start_date": "2020-01-01"}
    )
    client.post(
        f"/api/purchase-orders/{older_po['id']}/lines",
        json={"supplier_part_id": free_sp["id"], "quantity": 1, "price": 99.0},
    )

    report = client.get("/api/reports/stock-value").json()
    still = next(r for r in report["rows"] if r["count"] == 5)
    assert still["unit_price"] == 7.0
    assert still["basis_po_id"] == later_po["id"]


def _priced_part(client, sup, sku, price, pack_qty=1, date="2026-01-01"):
    """A plain part with one supplier part and one priced PO line."""
    part = client.post("/api/parts", json={"sku": sku, "description": sku}).json()
    sp = client.post(
        "/api/supplier-parts",
        json={
            "supplier_id": sup["id"],
            "sku": f"SP-{sku}",
            "part_id": part["id"],
            "description": "",
            "ean": "",
            "hyperlink": "",
            "pack_qty": pack_qty,
        },
    ).json()
    po = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
    ).json()
    client.patch(f"/api/purchase-orders/{po['id']}", json={"start_date": date})
    client.post(
        f"/api/purchase-orders/{po['id']}/lines",
        json={"supplier_part_id": sp["id"], "quantity": 1, "price": price},
    )
    return part


def test_part_price_cache(client):
    sup = client.post("/api/suppliers", json={"name": "PriceCo"}).json()

    # a bought part is priced by its latest PO line, per item (price / pack_qty)
    bolt = _priced_part(client, sup, "BOLT", price=10.0, pack_qty=100)
    assert client.get(f"/api/parts/{bolt['id']}").json()["estimated_price"] == 0.1

    # a later order repricing it updates the cache on write
    _priced_part(client, sup, "BOLT", price=1.0)  # unrelated part, same sku text
    sp2 = client.post(
        "/api/supplier-parts",
        json={
            "supplier_id": sup["id"],
            "sku": "SP-BOLT2",
            "part_id": bolt["id"],
            "description": "",
            "ean": "",
            "hyperlink": "",
            "pack_qty": 1,
        },
    ).json()
    po2 = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
    ).json()
    client.patch(f"/api/purchase-orders/{po2['id']}", json={"start_date": "2026-06-01"})
    client.post(
        f"/api/purchase-orders/{po2['id']}/lines",
        json={"supplier_part_id": sp2["id"], "quantity": 1, "price": 0.25},
    )
    assert client.get(f"/api/parts/{bolt['id']}").json()["estimated_price"] == 0.25

    # an assembly is the sum of its components * quantity, cached on BOM writes
    nut = _priced_part(client, sup, "NUT", price=0.05)
    asm = client.post("/api/parts", json={"sku": "ASM", "description": "widget"}).json()
    client.patch(f"/api/parts/{asm['id']}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": bolt["id"], "quantity": 4},
    )
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": nut["id"], "quantity": 2},
    )
    priced = client.get(f"/api/parts/{asm['id']}").json()
    assert priced["estimated_price"] == pytest.approx(4 * 0.25 + 2 * 0.05)
    assert priced["price_partial"] is False

    # nesting: an assembly inside an assembly rolls up recursively
    outer = client.post(
        "/api/parts", json={"sku": "OUT", "description": "outer"}
    ).json()
    client.patch(f"/api/parts/{outer['id']}", json={"assembly": True})
    client.post(
        f"/api/parts/{outer['id']}/bom",
        json={"component_part_id": asm["id"], "quantity": 3},
    )
    assert client.get(f"/api/parts/{outer['id']}").json()[
        "estimated_price"
    ] == pytest.approx(3 * 1.1)

    # a change deep in the tree propagates up to every ancestor
    po3 = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
    ).json()
    client.patch(f"/api/purchase-orders/{po3['id']}", json={"start_date": "2026-09-01"})
    client.post(
        f"/api/purchase-orders/{po3['id']}/lines",
        json={"supplier_part_id": sp2["id"], "quantity": 1, "price": 0.5},
    )
    assert client.get(f"/api/parts/{asm['id']}").json()[
        "estimated_price"
    ] == pytest.approx(2.1)
    assert client.get(f"/api/parts/{outer['id']}").json()[
        "estimated_price"
    ] == pytest.approx(6.3)

    # an unpriced component makes the roll-up a partial floor, not a null
    mystery = client.post("/api/parts", json={"sku": "MYST", "description": "?"}).json()
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": mystery["id"], "quantity": 1},
    )
    partial = client.get(f"/api/parts/{asm['id']}").json()
    assert partial["estimated_price"] == pytest.approx(2.1)  # the priced part only
    assert partial["price_partial"] is True
    assert client.get(f"/api/parts/{outer['id']}").json()["price_partial"] is True


def test_stock_keeps_its_own_purchase_price(client):
    """Stock is worth what its own order paid, not the part's latest price:
    two batches of one part bought at different prices stay different."""
    sup = client.post("/api/suppliers", json={"name": "TwoPriceCo"}).json()
    part = client.post(
        "/api/parts", json={"sku": "WIDGET", "description": "widget"}
    ).json()
    sp = client.post(
        "/api/supplier-parts",
        json={
            "supplier_id": sup["id"],
            "sku": "SP-W",
            "part_id": part["id"],
            "description": "",
            "ean": "",
            "hyperlink": "",
            "pack_qty": 1,
        },
    ).json()

    def receive(price, date, qty):
        po = client.post(
            "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
        ).json()
        client.patch(f"/api/purchase-orders/{po['id']}", json={"start_date": date})
        line = client.post(
            f"/api/purchase-orders/{po['id']}/lines",
            json={"supplier_part_id": sp["id"], "quantity": qty, "price": price},
        ).json()[0]
        client.post(f"/api/po-lines/{line['id']}/book", json={"quantity": qty})
        return po

    cheap = receive(2.0, "2026-01-01", 10)
    dear = receive(5.0, "2026-06-01", 10)

    rows = {
        r["item_id"]: r for r in client.get("/api/reports/stock-value").json()["rows"]
    }
    by_po = {r["basis_po_id"]: r for r in rows.values()}
    # each batch keeps its own price -- the later 5.00 order does not reprice
    # the stock bought at 2.00
    assert by_po[cheap["id"]]["unit_price"] == 2.0
    assert by_po[cheap["id"]]["value"] == 20.0
    assert by_po[dear["id"]]["unit_price"] == 5.0
    assert by_po[dear["id"]]["value"] == 50.0
    assert all(r["basis"] == "po" for r in rows.values())

    # while the *part* estimate is the latest price, used only as a fallback
    assert client.get(f"/api/parts/{part['id']}").json()["estimated_price"] == 5.0
    loose = client.post("/api/stock", json={"part_id": part["id"]}).json()
    client.patch(f"/api/stock/{loose['id']}", json={"count": 4})
    row = next(
        r
        for r in client.get("/api/reports/stock-value").json()["rows"]
        if r["item_id"] == loose["id"]
    )
    assert row["basis"] == "estimate" and row["unit_price"] == 5.0


def test_virtual_part_price(client):
    """A virtual part (labour) is never purchased: its price is set by hand and
    feeds the assemblies that use it."""
    sup = client.post("/api/suppliers", json={"name": "VirtCo"}).json()
    bolt = _priced_part(client, sup, "VBOLT", price=1.5)
    labour = client.post(
        "/api/parts", json={"sku": "LABOUR", "description": "hour", "virtual": True}
    ).json()
    assert labour["virtual"] is True and labour["estimated_price"] is None

    asm = client.post("/api/parts", json={"sku": "VASM", "description": "thing"}).json()
    client.patch(f"/api/parts/{asm['id']}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": bolt["id"], "quantity": 2},
    )
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": labour["id"], "quantity": 0.5},
    )

    # unpriced labour leaves the roll-up partial
    before = client.get(f"/api/parts/{asm['id']}").json()
    assert before["estimated_price"] == 3.0 and before["price_partial"] is True

    # setting the labour rate completes it, and cascades into the assembly
    r = client.patch(f"/api/parts/{labour['id']}", json={"estimated_price": 40.0})
    assert r.json()["estimated_price"] == 40.0
    after = client.get(f"/api/parts/{asm['id']}").json()
    assert after["estimated_price"] == pytest.approx(3.0 + 0.5 * 40.0)
    assert after["price_partial"] is False

    # clearing it goes back to partial
    client.patch(f"/api/parts/{labour['id']}", json={"estimated_price": None})
    assert client.get(f"/api/parts/{asm['id']}").json()["price_partial"] is True

    # only virtual parts take a hand-set price
    r = client.patch(f"/api/parts/{bolt['id']}", json={"estimated_price": 9.0})
    assert r.status_code == 400 and "not virtual" in r.json()["detail"]
    assert client.get(f"/api/parts/{bolt['id']}").json()["estimated_price"] == 1.5


def test_build_produced_stock_costed_from_consumption(client):
    """Assembly stock produced by a build is worth what that build consumed,
    not the part's BOM estimate."""
    sup = client.post("/api/suppliers", json={"name": "BuildCo"}).json()
    # two components, bought at known prices
    a = _priced_part(client, sup, "COMP-A", price=3.0)
    b = _priced_part(client, sup, "COMP-B", price=0.5)

    def stock(part, qty, price, date):
        sp = client.get("/api/supplier-parts").json()
        spid = next(x["id"] for x in sp if x["part_id"] == part["id"])
        po = client.post(
            "/api/purchase-orders", json={"description": "d", "supplier_id": sup["id"]}
        ).json()
        client.patch(f"/api/purchase-orders/{po['id']}", json={"start_date": date})
        line = client.post(
            f"/api/purchase-orders/{po['id']}/lines",
            json={"supplier_part_id": spid, "quantity": qty, "price": price},
        ).json()[0]
        client.post(f"/api/po-lines/{line['id']}/book", json={"quantity": qty})

    stock(a, 10, 3.0, "2026-01-01")
    stock(b, 20, 0.5, "2026-01-01")

    asm = client.post(
        "/api/parts", json={"sku": "BASM", "description": "built thing"}
    ).json()
    client.patch(f"/api/parts/{asm['id']}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": a["id"], "quantity": 2},
    )
    client.post(
        f"/api/parts/{asm['id']}/bom",
        json={"component_part_id": b["id"], "quantity": 4},
    )

    build = client.post(
        "/api/build-orders",
        json={"description": "d", "part_id": asm["id"], "quantity": 3},
    ).json()
    client.patch(f"/api/build-orders/{build['id']}", json={"status": "Production"})
    r = client.post(f"/api/build-orders/{build['id']}/produce", json={"quantity": 3})
    assert r.is_success

    rows = {
        x["item_id"]: x for x in client.get("/api/reports/stock-value").json()["rows"]
    }
    produced = next(
        x
        for x in rows.values()
        if x["part_id"] == asm["id"] and x["status"] == "Available"
    )
    # consumed 3*(2 @ 3.00 + 4 @ 0.50) = 24.00 for 3 units -> 8.00 each
    assert produced["basis"] == "build"
    assert produced["unit_price"] == pytest.approx(8.0)
    assert produced["value"] == pytest.approx(24.0)

    # a shortage makes the cost a floor, not exact
    short_asm = client.post(
        "/api/parts", json={"sku": "SHORT", "description": "short"}
    ).json()
    client.patch(f"/api/parts/{short_asm['id']}", json={"assembly": True})
    client.post(
        f"/api/parts/{short_asm['id']}/bom",
        json={"component_part_id": a["id"], "quantity": 100},  # far more than stocked
    )
    sb = client.post(
        "/api/build-orders",
        json={"description": "d", "part_id": short_asm["id"], "quantity": 1},
    ).json()
    client.patch(f"/api/build-orders/{sb['id']}", json={"status": "Production"})
    client.post(f"/api/build-orders/{sb['id']}/produce", json={"quantity": 1})
    short_row = next(
        x
        for x in client.get("/api/reports/stock-value").json()["rows"]
        if x["part_id"] == short_asm["id"] and x["status"] == "Available"
    )
    assert short_row["basis"] == "build_partial"


def test_assemblies_cannot_be_purchased(client):
    sup = client.post("/api/suppliers", json={"name": "NoAsmCo"}).json()
    asm = client.post("/api/parts", json={"sku": "ASM2", "description": "built"}).json()
    client.patch(f"/api/parts/{asm['id']}", json={"assembly": True})

    r = client.post(
        "/api/supplier-parts",
        json={
            "supplier_id": sup["id"],
            "sku": "SP-ASM",
            "part_id": asm["id"],
            "description": "",
            "ean": "",
            "hyperlink": "",
            "pack_qty": 1,
        },
    )
    assert r.status_code == 400 and "built, not purchased" in r.json()["detail"]

    # and the inverse: a part a supplier sells cannot become an assembly
    bought = _priced_part(client, sup, "BOUGHT", price=1.0)
    r = client.patch(f"/api/parts/{bought['id']}", json={"assembly": True})
    assert r.status_code == 400 and "built, not purchased" in r.json()["detail"]


def test_openapi_schema_generates(client):
    """The frontend's typed client is built from this; it must be valid."""
    schema = client.get("/openapi.json").json()
    assert "/api/parts" in schema["paths"]
    assert "PartOut" in schema["components"]["schemas"]


def test_part_demand_and_suggested_order(client):
    asm = client.post("/api/parts", json={"sku": "ASM", "description": "a"}).json()[
        "id"
    ]
    comp = client.post("/api/parts", json={"sku": "C", "description": "c"}).json()["id"]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    client.post(
        f"/api/parts/{asm}/bom", json={"component_part_id": comp, "quantity": 2}
    )
    item = client.post("/api/stock", json={"part_id": comp}).json()
    client.patch(f"/api/stock/{item['id']}", json={"count": 3})

    build = client.post(
        "/api/build-orders", json={"description": "d", "part_id": asm, "quantity": 4}
    ).json()
    # a Draft build is a scratchpad and asks for nothing yet
    assert build["status"] == "Draft"
    assert client.get(f"/api/parts/{comp}").json()["needed"] == 0
    # planning it (Pending) already books the demand, before production starts
    client.patch(f"/api/build-orders/{build['id']}", json={"status": "Pending"})
    assert client.get(f"/api/parts/{comp}").json()["needed"] == 8
    client.patch(f"/api/build-orders/{build['id']}", json={"status": "Production"})

    # 4 units x 2 each = 8 needed, 3 in stock, nothing on order
    row = client.get(f"/api/parts/{comp}").json()
    assert (row["needed"], row["in_stock"], row["incoming"]) == (8, 3, 0)
    assert row["suggested_order"] == 5

    # order 2 packs of 2 -> 4 incoming, suggestion drops to 1
    sup = client.post("/api/suppliers", json={"name": "S"}).json()["id"]
    sp = client.post(
        "/api/supplier-parts",
        json={
            "description": "d",
            "supplier_id": sup,
            "sku": "SP",
            "part_id": comp,
            "pack_qty": 2,
        },
    ).json()["id"]
    po = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup}
    ).json()["id"]
    client.post(
        f"/api/purchase-orders/{po}/lines",
        json={"supplier_part_id": sp, "quantity": 2, "price": 1.0},
    )
    row = client.get(f"/api/parts/{comp}").json()
    assert (row["incoming"], row["suggested_order"]) == (4, 1)

    # producing 1 unit consumes 2 and drops the remaining need to 6
    client.post(f"/api/build-orders/{build['id']}/produce", json={"quantity": 1})
    row = client.get(f"/api/parts/{comp}").json()
    assert (row["needed"], row["in_stock"]) == (6, 1)
    assert row["suggested_order"] == 1


def test_order_references_default_and_stay_unique(client):
    """Both order types get a BO-/PO-<pk> code when none is given, and no two
    orders may carry the same code (case-insensitively)."""
    sup = client.post("/api/suppliers", json={"name": "Acme"}).json()["id"]
    asm = client.post("/api/parts", json={"sku": "ASM", "description": "a"}).json()[
        "id"
    ]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})

    po = client.post(
        "/api/purchase-orders", json={"description": "d", "supplier_id": sup}
    ).json()
    build = client.post(
        "/api/build-orders",
        json={"description": "d", "part_id": asm, "quantity": 1},
    ).json()
    assert po["reference"] == f"PO-{po['id']:04d}"
    assert build["reference"] == f"BO-{build['id']:04d}"

    # a second order cannot claim a code already in use, in either direction
    dup = client.post(
        "/api/purchase-orders",
        json={"description": "d", "supplier_id": sup, "reference": po["reference"]},
    )
    assert dup.status_code == 400 and "already used" in dup.json()["detail"]
    dup_build = client.post(
        "/api/build-orders",
        json={
            "description": "d",
            "part_id": asm,
            "quantity": 1,
            "reference": build["reference"].lower(),
        },
    )
    assert dup_build.status_code == 400

    # editing onto a taken code is rejected too; blanking keeps the stored code
    other = client.post(
        "/api/build-orders",
        json={"description": "d", "part_id": asm, "quantity": 1},
    ).json()
    assert (
        client.patch(
            f"/api/build-orders/{other['id']}", json={"reference": build["reference"]}
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/build-orders/{other['id']}", json={"reference": " "}
        ).json()["reference"]
        == other["reference"]
    )
    # keeping its own code is fine (the check must skip the row itself)
    assert (
        client.patch(
            f"/api/build-orders/{other['id']}", json={"reference": other["reference"]}
        ).status_code
        == 200
    )


def test_stock_log_reconstructs_deleted_production(client):
    """InvenTree deletes fully-used stock, so a migrated build can leave no row
    behind; the log reconstructs it from imported_produced without
    double-counting the units whose rows did survive."""
    asm = client.post("/api/parts", json={"sku": "A", "description": "asm"}).json()[
        "id"
    ]
    client.patch(f"/api/parts/{asm}", json={"assembly": True})
    bid = client.post(
        "/api/build-orders", json={"description": "d", "part_id": asm, "quantity": 10}
    ).json()["id"]
    # only the importer sets this, so there is no route to go through
    with db.session() as s:
        db.get_build(s, bid).imported_produced = 10
        s.commit()

    def produced():
        rows = client.get(f"/api/parts/{asm}/stock-log").json()
        return [e for e in rows if e["kind"] == "produced"]

    # nothing survived: the whole batch is reconstructed, with no row to link to
    assert [(e["quantity"], e["item_id"]) for e in produced()] == [(10.0, None)]

    # a surviving row reports itself, so only the remainder is reconstructed
    client.post("/api/stock", json={"part_id": asm, "count": 4, "build_id": bid})
    assert sum(e["quantity"] for e in produced()) == 10.0
