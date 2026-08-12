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

// Suggestions for the stocktake reason, which is a free string on the backend.
export const STOCKTAKE_REASONS = ['Damaged', 'Warranty claim by customer', 'Lost'];

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
	s.po_id !== null ? s.po_reference || `PO-${s.po_id}` : s.build_reference;

export function stockOrderUrl(s: StockOrigin) {
	if (s.po_id !== null) return `/purchase-orders/${s.po_id}`;
	const build = s.build_id ?? s.consumed_by_build_id;
	return build === null ? '' : `/build-orders/${build}`;
}
