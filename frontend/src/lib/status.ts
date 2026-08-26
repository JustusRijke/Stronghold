// InvenTree status labels for PO/build "status" fields. Hardcoded, not from
// the generated STATUS_OPTIONS in validators.ts: that generator keys picklists
// by field name, so PO, build and stock "status" collapse into one export
// (stock's wins). See backend api.py / db.py for the source of truth.
export const PO_STATUS_OPTIONS = [
  "Pending",
  "Placed",
  "On Hold",
  "Complete",
  "Cancelled",
  "Lost",
  "Returned",
];

export const BUILD_STATUS_OPTIONS = [
  "Draft",
  "Pending",
  "Production",
  "Cancelled",
  "Complete",
];
// builds in a finished state, hidden by default wherever builds are listed
export const BUILD_DONE = ["Complete", "Cancelled"];

// Sales order status: WooCommerce's own labels, imported and never set here.
// Hardcoded for the same reason as the two above -- a "status" field on any DTO
// collapses into the one generated STATUS_OPTIONS.
export const SO_STATUS_OPTIONS = [
  "pending",
  "processing",
  "on-hold",
  "completed",
  "cancelled",
  "refunded",
  "failed",
];
// sales in a finished state, hidden by default wherever sales are listed
export const SO_DONE = ["completed", "cancelled", "refunded", "failed"];
const SO_STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  processing: "Processing",
  "on-hold": "On hold",
  completed: "Completed",
  cancelled: "Cancelled",
  refunded: "Refunded",
  failed: "Failed",
};
export const soStatusLabel = (v: string) => SO_STATUS_LABELS[v] ?? v;

// Stock status: the backend stores short codes (models.StockStatus), the user
// sees these words. Keeping the two apart is why a rewording is not a data
// migration -- unlike the PO/build statuses above, which still store their label.
export const STOCK_AVAILABLE = "available";
export const STOCK_CONSUMED = "consumed";
export const STOCK_STATUS_OPTIONS = [STOCK_AVAILABLE, STOCK_CONSUMED];
const STOCK_STATUS_LABELS: Record<string, string> = {
  [STOCK_AVAILABLE]: "Available",
  [STOCK_CONSUMED]: "Consumed",
};
export const stockStatusLabel = (v: string) => STOCK_STATUS_LABELS[v] ?? v;

// Where a stock row came from: the PO it was received on, else the build that
// produced it, else (shortfall debt rows have neither) the order that owes it --
// a build or a sale.
// the consumed-by fields are optional: the stock-value report projects rows
// without them, and a row it cannot attribute simply shows no order.
type StockOrigin = {
  po_id: number | null;
  po_reference: string;
  build_id: number | null;
  consumed_by_build_id: number | null;
  consumed_by_so_id?: number | null;
  build_reference: string;
  consumed_by_reference?: string;
};

export const stockOrderLabel = (s: StockOrigin) =>
  s.po_id !== null
    ? s.po_reference
    : s.build_reference || (s.consumed_by_reference ?? "");

// The order that consumed a stock row -- a build that ate it or a sale that
// shipped it, never both. "" while it is still on the shelf.
export function stockConsumerUrl(s: StockOrigin) {
  if (s.consumed_by_build_id !== null)
    return `/build-orders/${s.consumed_by_build_id}`;
  return s.consumed_by_so_id == null
    ? ""
    : `/sales-orders/${s.consumed_by_so_id}`;
}

export function stockOrderUrl(s: StockOrigin) {
  if (s.po_id !== null) return `/purchase-orders/${s.po_id}`;
  const build = s.build_id ?? s.consumed_by_build_id;
  if (build !== null) return `/build-orders/${build}`;
  return s.consumed_by_so_id == null
    ? ""
    : `/sales-orders/${s.consumed_by_so_id}`;
}

// The "Created" column, identical in every stock table. The raw ISO value stays
// in the row so DataTable sorts it correctly; only the display is formatted.
export const CREATED_COLUMN = {
  key: "created_at" as const,
  header: "Created",
  width: "120px",
  // null for stock nothing could date (see db._to_v7): blank, not a guess
  format: (v: unknown) => (v == null ? "--" : new Date(String(v)).toLocaleDateString()),
};
