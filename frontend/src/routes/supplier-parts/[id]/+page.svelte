<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { supplierPartTabs } from '$lib/tabs.svelte';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import type { Part, PartPurchaseOrder, POLine, PurchaseOrder, StockItem, SupplierPart } from '$lib/types';
	import { PO_STATUS_OPTIONS } from '$lib/status';
	import { STATUS_OPTIONS as STOCK_STATUS_OPTIONS } from '$lib/validators';

	const id = $derived(Number($page.params.id));

	let sp = $state<SupplierPart | null>(null);
	let notFound = $state(false);
	let parts = $state<Part[]>([]);
	let supplierName = $state('');
	let pos = $state<PartPurchaseOrder[]>([]);
	let stock = $state<StockItem[]>([]);
	// same definition as the backend's in_stock: Available rows only, so a
	// build's outstanding debt (a negative row) nets out
	const inStock = $derived(
		stock.filter((s) => s.status === 'Available').reduce((sum, s) => sum + s.count, 0)
	);

	const sections = [
		{ id: 'details', label: 'Details' },
		{ id: 'purchase-orders', label: 'Purchase orders' },
		{ id: 'stock', label: 'Stock' }
	];

	async function load() {
		notFound = false;
		try {
			sp = await api.supplierPart(id);
		} catch {
			sp = null;
			notFound = true;
			return;
		}
		supplierPartTabs.open(id, sp.description ? `${sp.sku} - ${sp.description}` : sp.sku || `#${id}`);
		const [supplier, spPos, allStock, allParts] = await Promise.all([
			api.supplier(sp.supplier_id),
			api.supplierPartPos(id),
			api.stock(),
			api.parts()
		]);
		supplierName = supplier.name;
		pos = spPos;
		stock = allStock.filter((s) => s.part_id === sp!.part_id);
		const active = allParts.filter((p) => p.active && p.purchasable && !p.assembly);
		// keep the linked part selectable even if it is now inactive
		if (sp && !active.some((p) => p.id === sp!.part_id)) {
			const linked = await api.part(sp.part_id);
			active.push(linked);
		}
		parts = active;
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	function plabel(p: Part) {
		return p.sku ? `${p.sku} - ${p.description}` : p.description;
	}

	const partLabel = $derived.by(() => {
		const p = parts.find((p) => p.id === sp?.part_id);
		return p ? plabel(p) : `#${sp?.part_id}`;
	});

	async function save(patch: Partial<SupplierPart>) {
		if (await toast.run(() => api.patchSupplierPart(id, patch))) load();
	}

	// "add PO" dialog: ask how many to purchase, then either bump an existing
	// line on an open PO (Pending/Placed/On Hold) for this supplier part, add a
	// new line to one of those POs, or start a fresh PO+line.
	let poDialog = $state<HTMLDialogElement | null>(null);
	let poQty = $state(1);
	let poPrice = $state(0);
	let poCandidates = $state<{ po: PurchaseOrder; line: POLine | null }[]>([]);
	const OPEN_STATUSES = new Set(['Pending', 'Placed', 'On Hold']);

	async function addPo() {
		const allPos = await api.pos();
		const candidatePos = allPos.filter(
			(po) => po.supplier_id === sp!.supplier_id && OPEN_STATUSES.has(po.status)
		);
		const linesByPo = await Promise.all(candidatePos.map((po) => api.poLines(po.id)));
		poCandidates = candidatePos.map((po, i) => ({
			po,
			line: linesByPo[i].find((l) => l.supplier_part_id === id) ?? null
		}));
		poQty = 1;
		// a line is priced per pack; the part estimate is per unit
		const est = parts.find((p) => p.id === sp!.part_id)?.estimated_price ?? 0;
		poPrice = Number((est * (sp!.pack_qty || 1)).toFixed(4));
		poDialog?.showModal();
	}
	async function addToExistingLine(line: POLine, qty: number) {
		// only the quantity: the existing line keeps the price it was placed at
		if (await toast.run(() => api.editPoLine(line.id, { quantity: line.quantity + qty }))) {
			poDialog?.close();
			toast.show('Added to existing PO line', 'ok');
			pos = await api.supplierPartPos(id);
		}
	}
	async function addAsNewLine(po: PurchaseOrder, qty: number) {
		if (
			await toast.run(() =>
				api.addPoLine(po.id, { supplier_part_id: id, quantity: qty, price: poPrice })
			)
		) {
			poDialog?.close();
			goto(`/purchase-orders/${po.id}`);
		}
	}
	async function addAsNewPo(qty: number) {
		try {
			const po = await api.createPo({ supplier_id: sp!.supplier_id });
			await api.addPoLine(po.id, { supplier_part_id: id, quantity: qty, price: poPrice });
			poDialog?.close();
			goto(`/purchase-orders/${po.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}

	const poCols: Column<PartPurchaseOrder>[] = [
		{ key: 'reference', header: 'PO', mono: true, width: '150px' },
		{ key: 'supplier_reference', header: 'Supplier ref', truncate: true },
		{ key: 'quantity', header: 'Qty', mono: true, width: '90px' },
		{
			key: 'unit_price',
			header: 'Unit price',
			mono: true,
			width: '110px',
			format: (v) => (v === null ? '-' : (v as number).toFixed(4))
		},
		{ key: 'start_date', header: 'Ordered', width: '120px' },
		{
			key: 'status',
			header: 'Status',
			width: '120px',
			statusFilter: true,
			statusOptions: PO_STATUS_OPTIONS
		},
		{ key: 'end_date', header: 'Target', width: '140px' }
	];
	const stockCols: Column<StockItem>[] = [
		{ key: 'id', header: '#', mono: true, width: '80px' },
		{ key: 'count', header: 'Count', mono: true, width: '110px' },
		{ key: 'po_reference', header: 'PO', mono: true, width: '140px' },
		{
			key: 'status',
			header: 'Status',
			width: '160px',
			statusFilter: true,
			statusOptions: [...STOCK_STATUS_OPTIONS],
			statusDefaultHide: ['Consumed by build order']
		}
	];
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No supplier part {id}.</p></div>
{:else if sp}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">{sp.description ? `${sp.sku} - ${sp.description}` : sp.sku}</h1>
				{#if !sp.active}<span class="badge">inactive</span>{/if}
			</div>
			<p>
				Supplier: <a href={`/suppliers/${sp.supplier_id}`}>{supplierName}</a>
				&middot; Part: <a href={`/parts/${sp.part_id}`}>{partLabel}</a>
			</p>

			<section id="details">
				<p class="muted">In stock: <strong>{inStock}</strong></p>
				<label class="field">
					<span>Description</span>
					<input value={sp.description} onblur={(e) => save({ description: e.currentTarget.value })} />
				</label>
				<label class="field">
					<span>Part</span>
					<select value={sp.part_id} onchange={(e) => save({ part_id: Number(e.currentTarget.value) })}>
						{#each parts as p (p.id)}<option value={p.id}>{plabel(p)}</option>{/each}
					</select>
				</label>
				<label class="field">
					<span>EAN</span>
					<input value={sp.ean} onblur={(e) => save({ ean: e.currentTarget.value })} />
				</label>
				<label class="field">
					<span>URL</span>
					<input value={sp.hyperlink} onblur={(e) => save({ hyperlink: e.currentTarget.value })} />
				</label>
				<label class="field">
					<span>Pack qty</span>
					<input
						type="number"
						min="1"
						value={sp.pack_qty}
						onblur={(e) => save({ pack_qty: Number(e.currentTarget.value) })}
					/>
				</label>
				<label class="check">
					<input
						type="checkbox"
						checked={sp.active}
						onchange={(e) => save({ active: e.currentTarget.checked })}
					/>
					Active
				</label>
				{#if sp.hyperlink}
					<p>URL: <a href={sp.hyperlink} target="_blank" rel="noreferrer">{sp.hyperlink}</a></p>
				{/if}
			</section>

			<section id="purchase-orders">
				<h2 class="h2">Purchase orders ({pos.length})</h2>
				<DataTable
					columns={poCols}
					rows={pos}
					href={(po) => `/purchase-orders/${po.id}`}
					storageKey={`/supplier-parts/${id}/pos`}
					defaultSort={{ key: 'start_date', dir: 'desc' }}
					onAdd={addPo}
				/>
			</section>
			<section id="stock">
				<h2 class="h2">Stock (this part) ({stock.length})</h2>
				<DataTable
					columns={stockCols}
					rows={stock}
					href={(s) => `/stock/${s.id}`}
					storageKey={`/supplier-parts/${id}/stock`}
					onAdd={() => goto(`/stock/new?part_id=${sp!.part_id}`)}
				/>
			</section>
		</div>
	</div>

	<dialog bind:this={poDialog} class="podialog">
		<h2 class="h2">Purchase this part</h2>
		<label class="field">
			<span>Quantity (packs of {sp.pack_qty})</span>
			<input type="number" min="1" step="1" bind:value={poQty} />
		</label>
		<label class="field">
			<span>Price per pack</span>
			<input type="number" min="0" step="any" bind:value={poPrice} />
		</label>
		{#if poCandidates.length > 0}
			<p class="muted">
				Open purchase orders exist for this supplier. Adding to an existing line keeps that
				line's price.
			</p>
			<ul>
				{#each poCandidates as c (c.po.id)}
					<li>
						<a href={`/purchase-orders/${c.po.id}`}>{c.po.reference || `PO ${c.po.id}`}</a>
						{#if c.line}
							<button class="btn" onclick={() => addToExistingLine(c.line!, poQty)}
								>Add to existing line ({c.line.quantity} &rarr; {c.line.quantity + poQty})</button
							>
						{:else}
							<button class="btn" onclick={() => addAsNewLine(c.po, poQty)}>Add as new line</button>
						{/if}
					</li>
				{/each}
			</ul>
			<p class="muted">Or start a new purchase order:</p>
		{/if}
		<div class="actions">
			<button class="btn ghost" onclick={() => poDialog?.close()}>Cancel</button>
			<button class="btn" onclick={() => addAsNewPo(poQty)}>New PO</button>
		</div>
	</dialog>
{/if}

<style>
	dialog.podialog {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 20px;
		min-width: 320px;
		background: var(--card);
		color: var(--ink);
	}
	dialog.podialog::backdrop {
		background: rgba(0, 0, 0, 0.4);
	}
	dialog.podialog ul {
		margin: 8px 0 0 20px;
	}
	dialog.podialog .actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
</style>
