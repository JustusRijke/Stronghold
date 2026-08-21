// Typed fetch client for the FastAPI backend. Every call returns parsed JSON or
// throws ApiError carrying the backend's `detail` message (from InventoryError).
import type {
	Part,
	BomLine,
	BomUsage,
	StockItem,
	Supplier,
	SupplierPart,
	PartPurchaseOrder,
	PurchaseOrder,
	POLine,
	BuildOrder,
	BuildLine,
	PartBuild,
	StockLogEntry,
	Setting,
	DeploymentSettings,
	Activity,
	SearchResult,
	StockValueReport,
	SalesOrder,
	PartSalesOrder,
	SalesOrderLine,
	SalesShortage,
	ImportResult
} from './types';

export class ApiError extends Error {
	status: number;
	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

// A domain rejection (400) has a plain string detail. A schema rejection (422)
// has a LIST of {loc, msg, ...} objects instead, which stringifies to
// "[object Object]" -- so pull the messages out and name the field they came
// from, since the user cannot see the request body.
function errorText(detail: unknown): string | undefined {
	if (typeof detail === 'string') return detail;
	if (!Array.isArray(detail)) return undefined;
	const parts = detail
		.map((e) => {
			const msg = typeof e?.msg === 'string' ? e.msg : null;
			if (!msg) return null;
			// loc is like ["body", "quantity"]; the last entry is the field
			const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : null;
			return field && field !== 'body' ? `${field}: ${msg}` : msg;
		})
		.filter(Boolean);
	return parts.length ? parts.join('; ') : undefined;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
	const res = await fetch(`/api${path}`, {
		method,
		headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (!res.ok) {
		let detail = res.statusText;
		try {
			detail = errorText((await res.json()).detail) ?? detail;
		} catch {
			/* non-JSON error body */
		}
		throw new ApiError(res.status, detail);
	}
	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

const get = <T>(p: string) => req<T>('GET', p);
const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b);
const patch = <T>(p: string, b: unknown) => req<T>('PATCH', p, b);
const put = <T>(p: string, b: unknown) => req<T>('PUT', p, b);
const del = <T>(p: string) => req<T>('DELETE', p);

const enc = encodeURIComponent;

export const api = {
	// parts
	parts: () => get<Part[]>('/parts'),
	part: (id: number) => get<Part>(`/parts/${id}`),
	partPos: (id: number) => get<PartPurchaseOrder[]>(`/parts/${id}/purchase-orders`),
	partUsedIn: (id: number) => get<BomUsage[]>(`/parts/${id}/used-in`),
	partBuilds: (id: number) => get<BuildOrder[]>(`/parts/${id}/builds`),
	partConsumedBy: (id: number) => get<PartBuild[]>(`/parts/${id}/consumed-by`),
	partStockLog: (id: number) => get<StockLogEntry[]>(`/parts/${id}/stock-log`),
	createPart: (b: { sku: string; description: string; virtual?: boolean }) =>
		post<Part>('/parts', b),
	patchPart: (
		id: number,
		b: Partial<
			Pick<Part, 'sku' | 'description' | 'active' | 'assembly' | 'virtual' | 'purchasable' | 'estimated_price'>
		>
	) =>
		patch<Part>(`/parts/${id}`, b),

	// bom
	bom: (partId: number) => get<BomLine[]>(`/parts/${partId}/bom`),
	addBom: (partId: number, b: { component_part_id: number; quantity: number }) =>
		post<BomLine[]>(`/parts/${partId}/bom`, b),
	setBomQty: (lineId: number, quantity: number) =>
		patch<{ ok: boolean }>(`/bom/${lineId}`, { quantity }),
	removeBom: (lineId: number) => del<{ ok: boolean }>(`/bom/${lineId}`),

	// stock
	stock: () => get<StockItem[]>('/stock'),
	stockItem: (id: number) => get<StockItem>(`/stock/${id}`),
	createStock: (b: { part_id: number }) => post<StockItem>('/stock', b),
	patchStock: (id: number, b: Partial<Pick<StockItem, 'count' | 'status'>>) =>
		patch<StockItem>(`/stock/${id}`, b),
	settleDebt: (id: number, b: { quantity: number; item_id?: number | null }) =>
		post<Part>(`/stock/${id}/settle`, b),
	stocktakeReasons: () =>
		get<{ add: string[]; subtract: string[] }>('/settings/stocktake-reasons'),
	stocktake: (b: {
		part_id: number;
		count: number;
		reason: string;
		item_id?: number | null;
		build_id?: number | null;
		po_id?: number | null;
	}) => post<Part>('/stock/stocktake', b),
	addNegativeStock: (b: {
		part_id: number;
		quantity: number;
		build_id: number;
		reason?: string;
	}) => post<Part>('/stock/negative', b),

	// suppliers
	suppliers: () => get<Supplier[]>('/suppliers'),
	supplier: (id: number) => get<Supplier>(`/suppliers/${id}`),
	supplierPartsOf: (id: number) => get<SupplierPart[]>(`/suppliers/${id}/parts`),
	supplierPos: (id: number) => get<PurchaseOrder[]>(`/suppliers/${id}/purchase-orders`),
	createSupplier: (b: { name: string }) => post<Supplier>('/suppliers', b),
	patchSupplier: (id: number, b: Partial<Pick<Supplier, 'name' | 'active'>>) =>
		patch<Supplier>(`/suppliers/${id}`, b),

	// supplier parts
	supplierParts: () => get<SupplierPart[]>('/supplier-parts'),
	supplierPart: (id: number) => get<SupplierPart>(`/supplier-parts/${id}`),
	supplierPartPos: (id: number) =>
		get<PartPurchaseOrder[]>(`/supplier-parts/${id}/purchase-orders`),
	createSupplierPart: (b: {
		supplier_id: number;
		sku: string;
		part_id: number;
		description: string;
		ean: string;
		hyperlink: string;
		pack_qty: number;
	}) => post<SupplierPart>('/supplier-parts', b),
	patchSupplierPart: (
		id: number,
		b: Partial<
			Pick<SupplierPart, 'part_id' | 'description' | 'ean' | 'hyperlink' | 'pack_qty' | 'active'>
		>
	) => patch<SupplierPart>(`/supplier-parts/${id}`, b),

	// purchase orders
	pos: () => get<PurchaseOrder[]>('/purchase-orders'),
	po: (id: number) => get<PurchaseOrder>(`/purchase-orders/${id}`),
	createPo: (b: {
		supplier_id: number;
		description: string;
		start_date?: string;
	}) =>
		post<PurchaseOrder>('/purchase-orders', b),
	patchPo: (id: number, b: Partial<Omit<PurchaseOrder, 'id' | 'supplier_id'>>) =>
		patch<PurchaseOrder>(`/purchase-orders/${id}`, b),
	poLines: (id: number) => get<POLine[]>(`/purchase-orders/${id}/lines`),
	addPoLine: (id: number, b: { supplier_part_id: number; quantity: number; price: number }) =>
		post<POLine[]>(`/purchase-orders/${id}/lines`, b),
	editPoLine: (lineId: number, b: { quantity?: number; price?: number }) =>
		patch<{ ok: boolean }>(`/po-lines/${lineId}`, b),
	removePoLine: (lineId: number) => del<{ ok: boolean }>(`/po-lines/${lineId}`),
	bookPoLine: (lineId: number, quantity: number) =>
		post<{ ok: boolean }>(`/po-lines/${lineId}/book`, { quantity }),
	receiveAllPo: (id: number) => post<{ ok: boolean }>(`/purchase-orders/${id}/receive-all`),

	// build orders
	builds: () => get<BuildOrder[]>('/build-orders'),
	build: (id: number) => get<BuildOrder>(`/build-orders/${id}`),
	createBuild: (b: {
		part_id: number;
		quantity: number;
		description: string;
	}) =>
		post<BuildOrder>('/build-orders', b),
	patchBuild: (id: number, b: Partial<Omit<BuildOrder, 'id' | 'part_id'>>) =>
		patch<BuildOrder>(`/build-orders/${id}`, b),
	produceBuild: (id: number, quantity: number) =>
		post<BuildOrder>(`/build-orders/${id}/produce`, { quantity }),
	buildLines: (id: number) => get<BuildLine[]>(`/build-orders/${id}/lines`),
	buildStock: (id: number) => get<StockItem[]>(`/build-orders/${id}/stock`),
	resyncBuildLines: (id: number) =>
		post<BuildOrder>(`/build-orders/${id}/resync-lines`, {}),

	// sales orders
	salesOrders: () => get<SalesOrder[]>('/sales-orders'),
	salesOrder: (id: number) => get<SalesOrder>(`/sales-orders/${id}`),
	salesOrderLines: (id: number) => get<SalesOrderLine[]>(`/sales-orders/${id}/lines`),
	addLinePart: (id: number, lineId: number, b: { part_id: number; quantity: number }) =>
		post<SalesOrderLine[]>(`/sales-orders/${id}/lines/${lineId}/parts`, b),
	editLinePart: (linkId: number, quantity: number) =>
		patch<{ ok: boolean }>(`/sales-orders/lines/parts/${linkId}`, { quantity }),
	removeLinePart: (linkId: number) => del<{ ok: boolean }>(`/sales-orders/lines/parts/${linkId}`),
	salesOrderShortages: (id: number) => get<SalesShortage[]>(`/sales-orders/${id}/shortages`),
	bookSalesOrder: (id: number) => post<SalesOrder>(`/sales-orders/${id}/book`, {}),
	salesOrderStock: (id: number) => get<StockItem[]>(`/sales-orders/${id}/stock`),
	partSalesOrders: (id: number) => get<PartSalesOrder[]>(`/parts/${id}/sales-orders`),
	importSalesOrders: (b: { after: string; before?: string | null }) =>
		post<ImportResult>('/sales-orders/import', b),

	// settings
	settings: () => get<Setting[]>('/settings'),
	deploymentSettings: () => get<DeploymentSettings>('/settings/deployment'),
	setSetting: (key: string, value: string) => put<Setting>(`/settings/${enc(key)}`, { value }),
	testWooCommerce: () => post<{ ok: boolean }>('/settings/woocommerce/test', {}),

	// reports
	stockValue: () => get<StockValueReport>('/reports/stock-value'),
	refreshPrices: () => post<{ priced: number }>('/parts/refresh-prices'),

	// activity log
	activity: () => get<Activity[]>('/activity'),

	// search
	search: (q: string, includeInactive = false) =>
		get<SearchResult[]>(`/search?q=${enc(q)}&include_inactive=${includeInactive}`)
};
