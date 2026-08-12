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

export const BUILD_STATUS_OPTIONS = ['Pending', 'Production', 'On Hold', 'Cancelled', 'Complete'];
