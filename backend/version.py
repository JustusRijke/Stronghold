"""What version of Stronghold this is, and what shape of data it speaks.

Two separate numbers, on purpose:

- APP_VERSION comes from the git tag (hatch-vcs). Informational: it answers
  "which Stronghold do I install to open this data file?".
- SCHEMA_VERSION is a hand-maintained integer, bumped only when the shape of
  the data changes. It is what decides whether a data file needs migrating,
  so it must not move just because a release was cut.
"""

import re
from importlib.metadata import PackageNotFoundError, version

# Bump when a change makes an older data file wrong, and add the matching entry
# to db._MIGRATIONS. 1 is the 1.0 baseline: the shape the app shipped 1.0 with.
# 2 dropped purchase_orders.reference / build_orders.reference (derived from the
# pk since; see models.po_ref).
# 3 stores the enum columns (stock status and price basis, PO and build status)
# as small ints instead of text -- see models.EnumCode.
# 4 added sales orders (sales_orders, sales_order_lines, sales_order_line_parts
# and stock_items.consumed_by_so_id). Purely additive, so there is nothing to
# migrate -- but the stamp still moves, so an older Stronghold refuses the file
# instead of dropping every sale on its next export.
# 5 made the sku columns optional and unique: blank skus become NULL (the unique
# indexes count NULLs as distinct, so "no sku" is not a clash). parts.sku is
# unique globally, supplier_parts.sku only within its supplier -- two suppliers
# may use the same code, one supplier may not.
# 6 added the optional bom_lines.note. Purely additive, like 4.
# 7 added stock_items.created_at. Additive, but NOT NULL, so unlike 4 and 6 it
# is not a no-op: replayed rows would all read as "created today". The step
# backfills them from the order that created them (db._to_v7).
# 8 added product_sku_parts, the sales-sku -> (part, quantity) map the line-part
# prefill reads. Purely additive, like 4 and 6.
SCHEMA_VERSION = 8

try:
    APP_VERSION = version("stronghold")
except PackageNotFoundError:
    # running from a source tree that was never `uv sync`ed
    APP_VERSION = "0.0.0+unknown"

# Untagged builds get a "1.0.1.dev3+gabc1234" suffix that changes every commit.
# Stamping that into the data file would rewrite the stamp (and, with
# auto_commit, raise a commit) on every developer build, so only X.Y.Z is used.
_RELEASE = re.match(r"\d+\.\d+\.\d+", APP_VERSION)
RELEASE_VERSION = _RELEASE.group(0) if _RELEASE else APP_VERSION
