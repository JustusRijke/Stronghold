// Typed fetch client for the FastAPI backend. Every call returns parsed JSON or
// throws ApiError carrying the backend's `detail` message (from InventoryError).
import type {
	Part,
	BomLine,
	BomUsage,
	StockItem,
	Supplier,
	SupplierPart,
	PurchaseOrder,
	POLine,
	BuildOrder,
	BuildLine,
	Setting,
	Activity,
	SearchResult,
	StockValueReport
} from './types';

export class ApiError extends Error {
	status: number;
	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
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
			detail = (await res.json()).detail ?? detail;
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
	partPos: (id: number) => get<PurchaseOrder[]>(`/parts/${id}/purchase-orders`),
	partUsedIn: (id: number) => get<BomUsage[]>(`/parts/${id}/used-in`),
	partBuilds: (id: number) => get<BuildOrder[]>(`/parts/${id}/builds`),
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
		get<PurchaseOrder[]>(`/supplier-parts/${id}/purchase-orders`),
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
	createPo: (b: { supplier_id: number; reference?: string; start_date?: string }) =>
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
	createBuild: (b: { part_id: number; quantity: number; reference?: string }) =>
		post<BuildOrder>('/build-orders', b),
	patchBuild: (id: number, b: Partial<Omit<BuildOrder, 'id' | 'part_id'>>) =>
		patch<BuildOrder>(`/build-orders/${id}`, b),
	produceBuild: (id: number, quantity: number) =>
		post<BuildOrder>(`/build-orders/${id}/produce`, { quantity }),
	buildLines: (id: number) => get<BuildLine[]>(`/build-orders/${id}/lines`),
	buildStock: (id: number) => get<StockItem[]>(`/build-orders/${id}/stock`),
	resyncBuildLines: (id: number) =>
		post<BuildOrder>(`/build-orders/${id}/resync-lines`, {}),

	// settings
	settings: () => get<Setting[]>('/settings'),
	setSetting: (key: string, value: string) => put<Setting>(`/settings/${enc(key)}`, { value }),

	// reports
	stockValue: () => get<StockValueReport>('/reports/stock-value'),
	refreshPrices: () => post<{ priced: number }>('/parts/refresh-prices'),

	// activity log
	activity: () => get<Activity[]>('/activity'),

	// search
	search: (q: string, includeInactive = false) =>
		get<SearchResult[]>(`/search?q=${enc(q)}&include_inactive=${includeInactive}`)
};
