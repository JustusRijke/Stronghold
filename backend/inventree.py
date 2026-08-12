"""Read-only InvenTree HTTP client. Stdlib urllib only (no new dependency);
nothing here mutates the InvenTree server."""

import base64
import json
import urllib.request
from urllib.parse import urlencode

_PAGE = 100


def _get(url: str, headers: dict[str, str]) -> dict:
    # S310: url is the operator's own InvenTree host from settings.toml, not user input
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    # generous: a large page of build lines is slow to compute server-side
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return json.loads(response.read())


def _token(base_url: str, username: str, password: str) -> str:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    data = _get(f"{base_url}/api/user/token/", {"Authorization": f"Basic {creds}"})
    return data["token"]


def _fetch_all(
    base_url: str, username: str, password: str, path: str, mapper, page: int = _PAGE
) -> list:
    """Fetch every result of a paginated InvenTree list endpoint, mapped.
    `page` raises the page size for endpoints whose cost is per-request rather
    than per-row (see fetch_build_lines)."""
    base_url = base_url.rstrip("/")
    headers = {"Authorization": f"Token {_token(base_url, username, password)}"}
    rows, offset = [], 0
    sep = "&" if "?" in path else "?"  # path may already carry a filter query
    while True:
        query = urlencode({"limit": page, "offset": offset})
        result = _get(f"{base_url}{path}{sep}{query}", headers)
        rows.extend(mapper(r) for r in result["results"])
        offset += page
        if not result["next"]:
            return rows


def fetch_parts(base_url: str, username: str, password: str) -> list[dict]:
    """Fetch all parts, mapped to our Part shape. Categories etc. are ignored."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/part/",
        lambda p: {
            "id": p["pk"],
            "sku": p["IPN"] or "",
            "description": p["name"],
            "active": p["active"],
            "assembly": p["assembly"],
            "virtual": p["virtual"],
            "purchasable": p["purchaseable"],  # InvenTree's spelling
            # variant bookkeeping: only reported on, never imported (see
            # migrate_inventree.py's redundant-template report)
            "is_template": p["is_template"],
            "variant_of": p["variant_of"],
            "in_stock": p["in_stock"],
        },
    )


def fetch_bom(base_url: str, username: str, password: str) -> list[dict]:
    """Fetch all BOM lines as {parent, component, quantity}. parent/component are
    InvenTree part pks, resolved to local ids by migrate_inventree.py."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/bom/",
        lambda b: {
            "parent": b["part"],
            "component": b["sub_part"],
            "quantity": b["quantity"],
        },
    )


def fetch_suppliers(base_url: str, username: str, password: str) -> list[dict]:
    """Supplier companies as {id, name, active}. id is the InvenTree pk."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/company/?is_supplier=true",
        lambda c: {"id": c["pk"], "name": c["name"], "active": c["active"]},
    )


def _pack_qty(value) -> int:
    # pack_quantity is a string like "100"; may carry a unit ("100 ml") in newer
    # InvenTree. Take the leading number; default 1 if unparseable.
    try:
        return int(float(str(value).split()[0]))
    except ValueError, IndexError:
        return 1


def fetch_supplier_parts(base_url: str, username: str, password: str) -> list[dict]:
    """Supplier parts as {id, supplier, part, sku, description, hyperlink,
    pack_qty}. supplier/part are InvenTree pks, resolved by the migration."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/company/part/",
        lambda p: {
            "id": p["pk"],
            "supplier": p["supplier"],
            "part": p["part"],
            "sku": p["SKU"] or "",
            "description": p["description"] or "",
            "hyperlink": p["link"] or "",
            "pack_qty": _pack_qty(p["pack_quantity"]),
        },
    )


def fetch_purchase_orders(base_url: str, username: str, password: str) -> list[dict]:
    """Purchase orders as {id, supplier, reference, supplier_reference, status,
    start_date, end_date}. Cancelled orders (status 40) are dropped. start_date
    falls back to issue_date when unset; end_date is InvenTree's target_date."""
    rows = _fetch_all(
        base_url,
        username,
        password,
        "/api/order/po/",
        lambda o: {
            "id": o["pk"],
            "supplier": o["supplier"],
            "reference": o["reference"] or "",
            "supplier_reference": o["supplier_reference"] or "",
            "status": o["status"],
            "status_text": o["status_text"] or "",
            "start_date": o["start_date"] or o["issue_date"] or "",
            "end_date": o["target_date"] or "",
        },
    )
    return [r for r in rows if r["status"] != 40]  # 40 = Cancelled


def fetch_po_lines(base_url: str, username: str, password: str) -> list[dict]:
    """PO lines as {id, order, supplier_part, quantity, received, price}.
    order/supplier_part are InvenTree pks, resolved by the migration."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/order/po-line/",
        lambda line: {
            "id": line["pk"],
            "order": line["order"],
            "supplier_part": line["part"],
            "quantity": line["quantity"],
            "received": line["received"],
            "price": float(line["purchase_price"] or 0),
        },
    )


def fetch_build_orders(base_url: str, username: str, password: str) -> list[dict]:
    """Build orders as {id, part, reference, status, quantity, start_date,
    end_date}. part is the assembly's InvenTree pk, resolved by the migration.
    Cancelled builds (status 30) are dropped. start_date falls back to
    creation_date; end_date is InvenTree's target_date."""
    rows = _fetch_all(
        base_url,
        username,
        password,
        "/api/build/",
        lambda b: {
            "id": b["pk"],
            "part": b["part"],
            "reference": b["reference"] or "",
            "status": b["status"],
            "status_text": b["status_text"] or "",
            "quantity": b["quantity"],
            # units InvenTree says the build made. Authoritative: InvenTree
            # deletes fully-used stock, so counting surviving rows under-reports
            # (most historical builds would read 0).
            "completed": b["completed"],
            "start_date": b["start_date"] or b["creation_date"] or "",
            "end_date": b["target_date"] or "",
        },
    )
    return [r for r in rows if r["status"] != 30]  # 30 = Cancelled


def fetch_part_price(
    base_url: str, username: str, password: str, part_id: int
) -> float | None:
    """A part's minimum price from InvenTree's per-part pricing resource, or
    None. Prefers the manual override (`override_min`, what the user typed) and
    falls back to the computed `overall_min`. Only used for virtual parts, whose
    price cannot be derived from purchase orders -- one request per part, so
    never call it for the whole catalog."""
    base_url = base_url.rstrip("/")
    headers = {"Authorization": f"Token {_token(base_url, username, password)}"}
    data = _get(f"{base_url}/api/part/{int(part_id)}/pricing/", headers)
    for key in ("override_min", "overall_min"):
        value = data.get(key)
        if value not in (None, ""):
            return float(value)
    return None


def fetch_build_consumption(base_url: str, username: str, password: str) -> list[dict]:
    """Stock consumed by build orders, as {id, part, consumed_by, build,
    purchase_order, quantity}. These are stock items InvenTree marked
    `consumed_by` a build -- they are not in_stock, so fetch_stock never returns
    them. They carry what each build actually used, which is what prices its
    output. `build` is the build that PRODUCED the row (set when the consumed
    stock was itself an assembly), a different thing from `consumed_by`."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/stock/?consumed=true",
        lambda s: {
            "id": s["pk"],
            "part": s["part"],
            "consumed_by": s["consumed_by"],
            "build": s["build"],
            "purchase_order": s["purchase_order"],
            "quantity": s["quantity"],
        },
    )


def fetch_build_lines(base_url: str, username: str, password: str) -> list[dict]:
    """Per-build BOM lines as {id, build, part, quantity}. This is the BOM as it
    stood for that build, which the part's current BOM may no longer match.
    `quantity` is the extended amount (per-unit x build quantity).

    Big pages on purpose: this endpoint costs ~1.7s per request whatever the
    page size (it computes available/allocated/on-order stock per line, and
    ignores `fields=`), so the whole set is 130s at 100/page but under 20s at
    1000. Cost is per request, so fetch few large pages."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/build/line/",
        lambda line: {
            "id": line["pk"],
            "build": line["build"],
            "part": line["part"],
            "quantity": line["quantity"],
        },
        page=1000,
    )


def fetch_stock(base_url: str, username: str, password: str) -> list[dict]:
    """In-stock items as {id, part, supplier_part, quantity}. Only available
    stock (in_stock=true); consumed/spent stock is excluded server-side."""
    return _fetch_all(
        base_url,
        username,
        password,
        "/api/stock/?in_stock=true",
        lambda s: {
            "id": s["pk"],
            "part": s["part"],
            "supplier_part": s["supplier_part"],
            "purchase_order": s["purchase_order"],
            "build": s["build"],  # the build that produced it, when built
            "quantity": s["quantity"],
        },
    )
