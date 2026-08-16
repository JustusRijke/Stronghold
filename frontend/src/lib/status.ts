// InvenTree status labels for PO/build "status" fields. Hardcoded, not from
// the generated STATUS_OPTIONS in validators.ts: that generator keys picklists
// by field name, so PO, build and stock "status" collapse into one export
// (stock's wins). See backend api.py / db.py for the source of truth.
export const PO_STATUS_OPTIONS = [
	'Pending',
	'Placed',
	'On Hold',
	'Complete',
	'Cancelled',
	'Lost',
	'Returned'
];

export const BUILD_STATUS_OPTIONS = ['Draft', 'Pending', 'Production', 'Cancelled', 'Complete'];
// builds in a finished state, hidden by default wherever builds are listed
export const BUILD_DONE = ['Complete', 'Cancelled'];

// Stock status: the backend stores short codes (models.StockStatus), the user
// sees these words. Keeping the two apart is why a rewording is not a data
// migration -- unlike the PO/build statuses above, which still store their label.
export const STOCK_AVAILABLE = 'available';
export const STOCK_CONSUMED = 'consumed';
export const STOCK_STATUS_OPTIONS = [STOCK_AVAILABLE, STOCK_CONSUMED];
const STOCK_STATUS_LABELS: Record<string, string> = {
	[STOCK_AVAILABLE]: 'Available',
	[STOCK_CONSUMED]: 'Consumed'
};
export const stockStatusLabel = (v: string) => STOCK_STATUS_LABELS[v] ?? v;

// Where a stock row came from: the PO it was received on, else the build that
// produced it, else (shortfall debt rows have neither) the build that owes it.
type StockOrigin = {
	po_id: number | null;
	po_reference: string;
	build_id: number | null;
	consumed_by_build_id: number | null;
	build_reference: string;
};

export const stockOrderLabel = (s: StockOrigin) =>
	s.po_id !== null ? s.po_reference : s.build_reference;

export function stockOrderUrl(s: StockOrigin) {
	if (s.po_id !== null) return `/purchase-orders/${s.po_id}`;
	const build = s.build_id ?? s.consumed_by_build_id;
	return build === null ? '' : `/build-orders/${build}`;
}
