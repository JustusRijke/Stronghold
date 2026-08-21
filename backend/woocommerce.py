"""Read-only WooCommerce HTTP client. Stdlib urllib only (no new dependency);
nothing here mutates the WooCommerce store.

Two things about the URL and auth are not obvious and cost a long diagnosis
once, so they are load-bearing:

- The path form must be `/wp-json/wc/v3/...`. WooCommerce decides whether to
  authenticate a request by looking for the literal `/wp-json/wc/` in the
  request URI, so the `?rest_route=/wc/v3/...` form (valid for WordPress core,
  and the only form that works with plain permalinks) never triggers key auth:
  every request arrives anonymous and 401s no matter what credentials it
  carries.
- Credentials go in an HTTP Basic header, key as user and secret as password.
"""

import base64
import json
import ssl
import urllib.request
from datetime import date
from urllib.parse import urlencode

_PAGE = 100


def _money(value) -> float:
    """WooCommerce is inconsistent about money: line `price` comes back as a
    JSON number while `total`/`subtotal`/`shipping_total` are strings, and a
    blank field is "". Everything lands as a float."""
    return float(value or 0)


def _get(url: str, headers: dict[str, str]) -> tuple[list | dict, dict]:
    # S310: url is the operator's own WooCommerce host from settings.toml
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    # ponytail: test/LAN stores commonly run a self-signed cert, and this
    # client only ever reads. Verify properly if it is ever pointed at a
    # store worth spoofing.
    context = ssl._create_unverified_context()  # noqa: S323
    with urllib.request.urlopen(request, timeout=120, context=context) as response:  # noqa: S310
        return json.loads(response.read()), dict(response.headers)


def _auth_header(key: str, secret: str) -> dict[str, str]:
    creds = base64.b64encode(f"{key}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _name(block: dict) -> str:
    return f"{block.get('first_name', '')} {block.get('last_name', '')}".strip()


def _map_order(o: dict) -> dict:
    """One WooCommerce order, mapped to what we store. Shipping may be entirely
    blank (digital orders, and any store that does not ask for an address), so
    both the customer name and the country fall back to billing."""
    shipping, billing = o.get("shipping") or {}, o.get("billing") or {}
    return {
        "wc_order_id": o["id"],
        "wc_number": str(o.get("number", "")),
        "status": o.get("status", ""),
        "date_created": (o.get("date_created") or "")[:10],
        "customer_name": _name(shipping) or _name(billing),
        "shipping_country": shipping.get("country") or billing.get("country") or "",
        "shipping_cost": _money(o.get("shipping_total")),
        "lines": [
            {
                "wc_line_id": li["id"],
                "sku": li.get("sku") or "",  # often empty; never matched on
                "description": li.get("name") or "",
                "unit_price": _money(li.get("price")),
                "quantity": float(li.get("quantity") or 0),
            }
            for li in o.get("line_items", [])
        ],
    }


def fetch_orders(
    base_url: str, key: str, secret: str, after: date, before: date | None = None
) -> list[dict]:
    """Every order created in the window, mapped to our shape. `after` and
    `before` are inclusive calendar dates."""
    headers = _auth_header(key, secret)
    root = f"{base_url.rstrip('/')}/wp-json/wc/v3/orders"
    params = {"per_page": _PAGE, "after": f"{after.isoformat()}T00:00:00"}
    if before is not None:
        params["before"] = f"{before.isoformat()}T23:59:59"

    orders, page = [], 1
    while True:
        result, headers_out = _get(
            f"{root}?{urlencode({**params, 'page': page})}", headers
        )
        orders.extend(_map_order(o) for o in result)
        # WooCommerce reports the page count; trust it rather than guessing
        # from a short page (a filtered final page can be exactly _PAGE long)
        if page >= int(headers_out.get("X-WP-TotalPages", 1) or 1):
            return orders
        page += 1
