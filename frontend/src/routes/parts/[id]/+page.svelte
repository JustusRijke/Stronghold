<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { partTabs } from '$lib/tabs.svelte';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import StocktakeDialog from '$lib/components/StocktakeDialog.svelte';
	import NegativeStockDialog from '$lib/components/NegativeStockDialog.svelte';
	import type { PartBuild, Part, BomLine, BomUsage, POLine, PartPurchaseOrder, PurchaseOrder, StockItem, StockLogEntry, Supplier, SupplierPart } from '$lib/types';

	type PartPO = PartPurchaseOrder & { supplier_name: string };
	import { BUILD_DONE, BUILD_STATUS_OPTIONS, PO_STATUS_OPTIONS, stockOrderLabel, stockOrderUrl } from '$lib/status';
	import { STATUS_OPTIONS as STOCK_STATUS_OPTIONS } from '$lib/validators';

	const id = $derived(Number($page.params.id));

	let part = $state<Part | null>(null);
	let notFound = $state(false);
	let stocktakeOpen = $state(false);
	let negStockOpen = $state(false);
	let stockLog = $state<StockLogEntry[]>([]);
	let bom = $state<BomLine[]>([]);
	let activeParts = $state<Part[]>([]);

	// related data (cross-links)
	let supplierParts = $state<(SupplierPart & { supplier_name: string })[]>([]);
	let stock = $state<StockItem[]>([]);
	let pos = $state<PartPO[]>([]);
	let usedIn = $state<BomUsage[]>([]);
	// an assembly lists the builds that make it; any other part lists the builds
	// that consume it (with what they require of it and have consumed so far)
	let builds = $state<PartBuild[]>([]);

	// new-bom-line inputs
	let newComp = $state<number | ''>('');
	let newQty = $state(1);

	// "add PO" dialog: ask how many to purchase, then either bump an existing
	// line on an open PO (Pending/Placed/On Hold) for this part's supplier(s),
	// add a new line to one of those POs, or start a fresh PO+line.
	let poDialog = $state<HTMLDialogElement | null>(null);
	// packs to order, per supplier part: pack size differs between them, so the
	// suggested unit count rounds up to a different number of packs for each
	let poQty = $state<Record<number, number>>({});
	// price per pack, defaulted per supplier part (pack size differs between them)
	let poPrice = $state<Record<number, number>>({});
	let poDescription = $state('');
	// candidates for the part's active supplier parts: existing open PO + line (if any)
	let poCandidates = $state<
		{ po: PurchaseOrder; supplierPart: SupplierPart & { supplier_name: string }; line: POLine | null }[]
	>([]);
	const OPEN_STATUSES = new Set(['Pending', 'Placed', 'On Hold']);

	// when purchasing from a supplier-part row, the dialog is limited to that one
	let poOnly = $state<number | null>(null);
	const poOffered = $derived(
		supplierParts.filter((sp) => sp.active && (poOnly === null || sp.id === poOnly))
	);

	async function addPo(only: SupplierPart | null = null) {
		poOnly = only?.id ?? null;
		const activeSp = poOffered;
		if (activeSp.length === 0) {
			goto('/purchase-orders/new');
			return;
		}
		const supplierIds = new Set(activeSp.map((sp) => sp.supplier_id));
		const allPos = await api.pos();
		const candidatePos = allPos.filter(
			(po) => supplierIds.has(po.supplier_id) && OPEN_STATUSES.has(po.status)
		);
		const linesByPo = await Promise.all(candidatePos.map((po) => api.poLines(po.id)));
		poCandidates = candidatePos.map((po, i) => {
			const sp = activeSp.find((sp) => sp.supplier_id === po.supplier_id)!;
			const line = linesByPo[i].find((l) => l.supplier_part_id === sp.id) ?? null;
			return { po, supplierPart: sp, line };
		});
		// prefill with enough packs to cover the shortfall (needed - stock - on order)
		const suggested = part?.suggested_order ?? 0;
		poQty = Object.fromEntries(
			activeSp.map((sp) => [sp.id, Math.max(1, Math.ceil(suggested / (sp.pack_qty || 1)))])
		);
		// a line is priced per pack; last_price and the part estimate are per unit
		const est = part?.estimated_price ?? 0;
		poPrice = Object.fromEntries(
			activeSp.map((sp) => [
				sp.id,
				Number(((sp.last_price ?? est) * (sp.pack_qty || 1)).toFixed(4))
			])
		);
		poDescription = part ? part.description || part.sku : '';
		poDialog?.showModal();
	}

	async function addToExistingLine(line: POLine, qty: number) {
		// only the quantity: the existing line keeps the price it was placed at
		if (await toast.run(() => api.editPoLine(line.id, { quantity: line.quantity + qty }))) {
			poDialog?.close();
			toast.show('Added to existing PO line', 'ok');
			load();
		}
	}
	async function addAsNewLine(po: PurchaseOrder, sp: SupplierPart, qty: number) {
		if (
			await toast.run(() =>
				api.addPoLine(po.id, { supplier_part_id: sp.id, quantity: qty, price: poPrice[sp.id] ?? 0 })
			)
		) {
			poDialog?.close();
			goto(`/purchase-orders/${po.id}`);
		}
	}
	async function addAsNewPo(sp: SupplierPart, qty: number) {
		try {
			const po = await api.createPo({
				supplier_id: sp.supplier_id,
				description: poDescription
			});
			await api.addPoLine(po.id, {
				supplier_part_id: sp.id,
				quantity: qty,
				price: poPrice[sp.id] ?? 0
			});
			poDialog?.close();
			goto(`/purchase-orders/${po.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}

	async function load() {
		notFound = false;
		try {
			part = await api.part(id);
		} catch {
			part = null;
			notFound = true;
			return;
		}
		// register (or relabel) this part's tab so it persists across reloads
		partTabs.open(id, part.description || part.sku || `Part ${id}`);
		const [
			bomLines,
			allParts,
			allSupplierParts,
			allStock,
			partPos,
			usage,
			allSuppliers,
			partBuilds,
			log
		] = await Promise.all([
			part.assembly ? api.bom(id) : Promise.resolve([]),
			api.parts(),
			api.supplierParts(),
			api.stock(),
			api.partPos(id),
			api.partUsedIn(id),
			api.suppliers(),
			part.assembly ? (api.partBuilds(id) as Promise<PartBuild[]>) : api.partConsumedBy(id),
			api.partStockLog(id)
		]);
		stockLog = log;
		bom = bomLines;
		activeParts = allParts.filter((p) => p.active);
		const supplierName = new Map(allSuppliers.map((s) => [s.id, s.name]));
		supplierParts = allSupplierParts
			.filter((sp) => sp.part_id === id)
			.map((sp) => ({ ...sp, supplier_name: supplierName.get(sp.supplier_id) ?? '' }));
		stock = allStock.filter((s) => s.part_id === id);
		pos = partPos.map((po) => ({ ...po, supplier_name: supplierName.get(po.supplier_id) ?? '' }));
		usedIn = usage;
		builds = partBuilds;
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	// sidebar sections, in document order; BOM only when the part is an assembly,
	// build orders last (history, and the longest table).
	// A virtual part is never bought or stocked, so it has no supplier parts,
	// stock or purchase orders to show. A non-purchasable part still holds
	// stock (a build can produce it), but is never bought.
	const buyable = $derived(!!part?.purchasable && !part?.assembly);
	const sections = $derived([
		{ id: 'details', label: 'Details' },
		...(part?.assembly ? [{ id: 'bom', label: 'BOM' }] : []),
		...(part?.virtual
			? []
			: [
					...(buyable ? [{ id: 'supplier-parts', label: 'Supplier parts' }] : []),
					{ id: 'stock', label: 'Stock items' },
					{ id: 'stock-log', label: 'Stock log' },
					...(buyable ? [{ id: 'purchase-orders', label: 'Purchase orders' }] : [])
				]),
		{ id: 'used-in', label: 'Used in' },
		{ id: 'build-orders', label: 'Build orders' }
	]);

	function label(p: Part) {
		return p.sku ? `${p.sku} - ${p.description}` : p.description;
	}

	const price = (v: number | null, partial = false) =>
		v === null ? '-' : `${v.toFixed(4)}${partial ? '*' : ''}`;

	const bomCols: Column<BomLine>[] = [
		{ key: 'component_sku', header: 'SKU', mono: true, width: '120px' },
		{ key: 'component_description', header: 'Component', truncate: true },
		{ key: 'quantity', header: 'Qty', edit: 'number', width: '110px' },
		{
			key: 'unit_price',
			header: 'Unit price',
			mono: true,
			width: '110px',
			format: (v, r) => price(v as number | null, r.price_partial)
		},
		{
			key: 'line_price',
			header: 'Line total',
			mono: true,
			width: '110px',
			format: (v, r) => price(v as number | null, r.price_partial)
		}
	];
	const spCols: Column<SupplierPart & { supplier_name: string }>[] = [
		{ key: 'sku', header: 'SKU', mono: true, width: '150px' },
		{ key: 'supplier_name', header: 'Supplier', width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'pack_qty', header: 'Pack', mono: true, width: '80px' },
		{
			key: 'last_price',
			header: 'Unit price',
			mono: true,
			width: '110px',
			format: (v) => (v === null ? '-' : (v as number).toFixed(4))
		},
		{ key: 'last_po_date', header: 'Last PO', mono: true, width: '120px' },
		{ key: 'active', header: 'Active', bool: true, boolPreset: true, width: '90px' }
	];
	// where a stock row came from: its PO, else the build that made or owes it
	type StockRow = StockItem & { order: string; order_url: string; nonzero: boolean };
	const stockRows = $derived<StockRow[]>(
		stock.map((s) => ({
			...s,
			order: stockOrderLabel(s),
			order_url: stockOrderUrl(s),
			nonzero: s.count !== 0
		}))
	);
	const EVENT_LABEL: Record<StockLogEntry['kind'], string> = {
		received: 'Received',
		produced: 'Produced',
		consumed: 'Consumed',
		stocktake: 'Stocktake',
		debt: 'Owed to build',
		unknown: 'Unknown'
	};
	// `when` keeps the raw ISO value so the column sorts chronologically; the ~
	// prefix is display only. Dates from an order are the order's date, not the
	// moment stock moved -- only a stocktake records the real time.
	const logRows = $derived(
		stockLog.map((e) => ({
			...e,
			when: e.at ?? '',
			event: EVENT_LABEL[e.kind],
			qty: e.drawn_down ? `${e.quantity} (left)` : String(e.quantity)
		}))
	);
	type LogRow = (typeof logRows)[number];
	// storageKey is versioned: `when` used to hold a display string, so a sort
	// saved against the old column would order the ISO dates wrongly
	const logCols: Column<LogRow>[] = [
		{
			key: 'when',
			header: 'When',
			mono: true,
			width: '150px',
			format: (v, r) =>
				!v ? '' : r.at_approx ? `~ ${String(v).slice(0, 10)}` : String(v).replace('T', ' ').slice(0, 16)
		},
		{ key: 'event', header: 'Event', width: '130px' },
		{ key: 'qty', header: 'Quantity', mono: true, width: '110px' },
		{
			key: 'order_label',
			header: 'Order',
			mono: true,
			width: '140px',
			cellHref: (r) => r.order_url
		},
		{ key: 'reason', header: 'Reason', truncate: true }
	];
	const stockCols: Column<StockRow>[] = [
		{ key: 'id', header: '#', mono: true, width: '80px' },
		{ key: 'count', header: 'Count', mono: true, width: '110px' },
		{ key: 'order', header: 'Order', mono: true, width: '140px', cellHref: (r) => r.order_url },
		{
			key: 'nonzero',
			header: 'In stock',
			width: '90px',
			bool: true,
			boolPreset: true // settled debts and fully-consumed receipts sit at zero
		},
		{
			key: 'status',
			header: 'Status',
			width: '160px',
			statusFilter: true,
			statusOptions: [...STOCK_STATUS_OPTIONS],
			statusDefaultHide: ['Consumed by build order']
		}
	];
	const poCols: Column<PartPO>[] = [
		{ key: 'reference', header: 'PO', mono: true, width: '150px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'supplier_name', header: 'Supplier', truncate: true },
		{ key: 'supplier_reference', header: 'Supplier ref', truncate: true },
		{ key: 'quantity', header: 'Qty', mono: true, width: '90px' },
		{
			key: 'unit_price',
			header: 'Unit price',
			mono: true,
			width: '110px',
			format: (v) => price(v as number | null)
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
	const buildCols: Column<PartBuild>[] = $derived([
		{ key: 'reference', header: 'Build', mono: true, width: '150px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'quantity', header: 'Qty', mono: true, width: '90px' },
		...(part?.assembly
			? ([{ key: 'produced', header: 'Produced', mono: true, width: '100px' }] as Column<PartBuild>[])
			: ([
					{ key: 'required', header: 'Required', mono: true, width: '100px' },
					{ key: 'consumed', header: 'Consumed', mono: true, width: '100px' }
				] as Column<PartBuild>[])),
		{
			key: 'status',
			header: 'Status',
			width: '120px',
			statusFilter: true,
			statusOptions: BUILD_STATUS_OPTIONS,
			// finished builds are history and outnumber the open ones; the status
			// filter unhides them
			statusDefaultHide: BUILD_DONE
		},
		{ key: 'end_date', header: 'Target', width: '140px' }
	]);
	const usedInCols: Column<BomUsage>[] = [
		{ key: 'parent_sku', header: 'Assembly', mono: true, width: '150px' },
		{ key: 'parent_description', header: 'Description', truncate: true },
		{ key: 'quantity', header: 'Qty', mono: true, width: '90px' }
	];

	async function saveSku(v: string) {
		if (await toast.run(() => api.patchPart(id, { sku: v }))) load();
	}
	async function saveDescription(v: string) {
		if (await toast.run(() => api.patchPart(id, { description: v }))) load();
	}
	async function savePrice(v: string) {
		// blank clears the price (back to "not set"), rather than meaning zero
		const price = v.trim() === '' ? null : Number(v);
		if (await toast.run(() => api.patchPart(id, { estimated_price: price }))) load();
	}
	async function toggleActive(v: boolean) {
		await toast.run(() => api.patchPart(id, { active: v }));
	}
	async function toggleAssembly(v: boolean) {
		if (await toast.run(() => api.patchPart(id, { assembly: v }))) load();
	}
	async function togglePurchasable(v: boolean) {
		if (await toast.run(() => api.patchPart(id, { purchasable: v }))) load();
	}
	async function toggleVirtual(v: boolean) {
		if (await toast.run(() => api.patchPart(id, { virtual: v }))) load();
	}
	async function addBomLine() {
		if (newComp === '') return;
		if (
			await toast.run(() => api.addBom(id, { component_part_id: Number(newComp), quantity: newQty }))
		) {
			newComp = '';
			newQty = 1;
			bom = await api.bom(id);
		}
	}
	async function setQty(lineId: number, q: number) {
		await toast.run(() => api.setBomQty(lineId, q));
	}
	async function removeLine(lineId: number) {
		if (!confirm('Remove this component?')) return;
		if (await toast.run(() => api.removeBom(lineId))) bom = await api.bom(id);
	}
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No part with id {id}.</p></div>
{:else if part}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">{part.description || part.sku || `Part ${part.id}`}</h1>
				{#if !part.active}<span class="badge">inactive</span>{/if}
			</div>

			<section id="details">
				<label class="field">
					<span>SKU</span>
					<input value={part.sku} onblur={(e) => saveSku(e.currentTarget.value)} />
				</label>
				<label class="field">
					<span>Description</span>
					<input value={part.description} onblur={(e) => saveDescription(e.currentTarget.value)} />
				</label>
				{#if !part.virtual}
					<p class="muted stockline">
						<span>
							In stock: <strong>{part.in_stock}</strong> &middot; needed for builds:
							<strong>{part.needed}</strong>
							&middot; on order: <strong>{part.incoming}</strong> &middot; suggested to order:
							<strong>{part.suggested_order}</strong>
						</span>
						<button class="btn ghost" type="button" onclick={() => (stocktakeOpen = true)}>
							Stocktake
						</button>
						<button class="btn ghost" type="button" onclick={() => (negStockOpen = true)}>
							Add negative stock item
						</button>
					</p>
				{/if}
				{#if part.virtual}
					<label class="field">
						<span>Price</span>
						<input
							type="number"
							min="0"
							step="any"
							value={part.estimated_price ?? ''}
							placeholder="not set"
							onblur={(e) => savePrice(e.currentTarget.value)}
						/>
					</label>
					<p class="muted hint">
						A virtual part is never purchased -- set its price here (e.g. an hourly rate). It is
						used to price the assemblies that list it.
					</p>
				{:else}
					<p class="muted">
						Estimated price:
						{#if part.estimated_price === null}
							<strong>unknown</strong>
							<span class="hint"
								>{part.assembly ? 'no BOM component is priced' : 'never purchased'}</span
							>
						{:else}
							<strong>{part.estimated_price.toFixed(4)}</strong>
							<span class="hint">
								{part.assembly ? 'BOM roll-up' : 'latest purchase order'}
								{#if part.price_partial}
									&mdash; <span class="warn">partial: some components have no price</span>
								{/if}
							</span>
						{/if}
					</p>
				{/if}
				<label class="check">
					<input
						type="checkbox"
						checked={part.assembly}
						disabled={part.virtual}
						onchange={(e) => toggleAssembly(e.currentTarget.checked)}
					/>
					Assembly
				</label>
				<label class="check">
					<input
						type="checkbox"
						checked={part.virtual}
						disabled={part.assembly}
						onchange={(e) => toggleVirtual(e.currentTarget.checked)}
					/>
					Virtual
				</label>
				<label class="check">
					<input
						type="checkbox"
						checked={part.purchasable}
						disabled={part.virtual || part.assembly}
						onchange={(e) => togglePurchasable(e.currentTarget.checked)}
					/>
					Purchasable
				</label>
				<label class="check">
					<input
						type="checkbox"
						checked={part.active}
						onchange={(e) => toggleActive(e.currentTarget.checked)}
					/>
					Active
				</label>
			</section>

			{#if part.assembly}
				<section id="bom">
					<h2 class="h2">
						BOM ({bom.length})
						{#if part.estimated_price !== null}
							<span class="hint">
								total {part.estimated_price.toFixed(4)}
								{#if part.price_partial}
									<span class="warn">&mdash; partial, some components have no price</span>
								{/if}
							</span>
						{/if}
					</h2>
					<DataTable
						columns={bomCols}
						rows={bom}
						href={(l) => `/parts/${l.component_part_id}`}
						storageKey={`/parts/${id}/bom`}
						onEdit={(line, _key, value) => setQty(line.id, Number(value))}
						onRemove={(line) => removeLine(line.id)}
					/>
					<div class="bomrow add">
						<select bind:value={newComp}>
							<option value="" disabled>Component…</option>
							{#each activeParts as p (p.id)}
								<option value={p.id}>{label(p)}</option>
							{/each}
						</select>
						<input class="qty" type="number" min="0" step="any" bind:value={newQty} />
						<button class="btn" onclick={addBomLine}>Add component</button>
					</div>
				</section>
			{/if}

			{#if !part.virtual}
				{#if buyable}
					<section id="supplier-parts">
						<h2 class="h2">Supplier parts ({supplierParts.length})</h2>
						<DataTable
							columns={spCols}
							rows={supplierParts}
							href={(sp) => `/supplier-parts/${sp.id}`}
							storageKey={`/parts/${id}/supplier-parts`}
							onAdd={() => goto(`/supplier-parts/new?part_id=${id}`)}
							rowAction={{ icon: '🛒', title: 'Purchase from this supplier', run: (sp) => addPo(sp) }}
						/>
					</section>
				{/if}
				<section id="stock">
					<h2 class="h2">Stock items ({stock.length})</h2>
					<DataTable
						columns={stockCols}
						rows={stockRows}
						href={(s) => `/stock/${s.id}`}
						storageKey={`/parts/${id}/stock`}
					/>
				</section>
				<section id="stock-log">
					<h2 class="h2">
						Stock log ({logRows.length})
						<span class="hint">what happened to this part's stock, newest first</span>
					</h2>
					<p class="muted hint">
						Dates marked ~ come from the purchase or build order, not the moment stock moved. Only a
						stocktake records the exact time.
					</p>
					<DataTable
						columns={logCols}
						rows={logRows}
						href={(r) => `/stock/${r.item_id}#${r.kind}`}
						storageKey={`/parts/${id}/stock-log-v2`}
						defaultSort={{ key: 'when', dir: 'desc' }}
					/>
				</section>
				{#if buyable}
					<section id="purchase-orders">
						<h2 class="h2">Purchase orders ({pos.length})</h2>
						<DataTable
							columns={poCols}
							rows={pos}
							href={(po) => `/purchase-orders/${po.id}`}
							storageKey={`/parts/${id}/pos`}
							defaultSort={{ key: 'start_date', dir: 'desc' }}
							onAdd={addPo}
						/>
					</section>
				{/if}
			{/if}
			<section id="used-in">
				<h2 class="h2">Used in assemblies ({usedIn.length})</h2>
				<DataTable
					columns={usedInCols}
					rows={usedIn}
					href={(u) => `/parts/${u.parent_part_id}`}
					storageKey={`/parts/${id}/used-in`}
				/>
			</section>
			<section id="build-orders">
				<h2 class="h2">Build orders ({builds.length})</h2>
				<DataTable
					columns={buildCols}
					rows={builds}
					href={(b) => `/build-orders/${b.id}`}
					storageKey={`/parts/${id}/builds`}
					defaultSort={{ key: 'end_date', dir: 'desc' }}
					onAdd={part.assembly ? () => goto(`/build-orders/new?part_id=${id}`) : undefined}
				/>
			</section>
		</div>
	</div>

	{#if part}
		<StocktakeDialog
			partId={id}
			partLabel={part.description}
			currentCount={part.in_stock}
			owed={part.owed}
			bind:open={stocktakeOpen}
			onsaved={load}
		/>
		<NegativeStockDialog
			partId={id}
			partLabel={part.description}
			bind:open={negStockOpen}
			onsaved={load}
		/>
	{/if}

	<dialog bind:this={poDialog} class="podialog">
		<h2 class="h2">Purchase this part</h2>
		{#if part}
			<p class="muted">
				In stock {part.in_stock} &middot; needed {part.needed} &middot; on order {part.incoming}
				&middot; <strong>suggested {part.suggested_order}</strong>
			</p>
		{/if}
		{#if poCandidates.length > 0}
			<p class="muted">
				Open purchase orders exist for this part's supplier(s). Adding to an existing line keeps
				that line's price.
			</p>
			<ul>
				{#each poCandidates as c (c.po.id)}
					<li>
						<a href={`/purchase-orders/${c.po.id}`}>{c.po.reference || `PO ${c.po.id}`}</a>
						<span class="muted">
							&middot; {c.supplierPart.supplier_name} &middot; packs of {c.supplierPart.pack_qty}
						</span>
						<input
							class="qty"
							type="number"
							min="1"
							step="1"
							title="Packs to order"
							bind:value={poQty[c.supplierPart.id]}
						/>
						{#if c.line}
							<button
								class="btn"
								onclick={() => addToExistingLine(c.line!, poQty[c.supplierPart.id])}
								>Add to existing line ({c.line.quantity} &rarr;
								{c.line.quantity + poQty[c.supplierPart.id]})</button
							>
						{:else}
							<input
								class="price"
								type="number"
								min="0"
								step="any"
								title="Price per pack"
								bind:value={poPrice[c.supplierPart.id]}
							/>
							<button
								class="btn"
								onclick={() => addAsNewLine(c.po, c.supplierPart, poQty[c.supplierPart.id])}
								>Add as new line</button
							>
						{/if}
					</li>
				{/each}
			</ul>
			<p class="muted">Or start a new purchase order:</p>
		{/if}
		<label class="field req">
			<span>New PO description</span>
			<input required bind:value={poDescription} placeholder="What this order is for" />
		</label>
		<ul>
			{#each poOffered as sp (sp.id)}
				<li>
					<span class="muted">{sp.supplier_name} &middot; packs of {sp.pack_qty}</span>
					<input
						class="qty"
						type="number"
						min="1"
						step="1"
						title="Packs to order"
						bind:value={poQty[sp.id]}
					/>
					<input
						class="price"
						type="number"
						min="0"
						step="any"
						title="Price per pack"
						bind:value={poPrice[sp.id]}
					/>
					<button
						class="btn"
						disabled={!poDescription.trim()}
						onclick={() => addAsNewPo(sp, poQty[sp.id])}>New PO</button
					>
				</li>
			{/each}
		</ul>
		<div class="actions">
			<button class="btn ghost" onclick={() => poDialog?.close()}>Cancel</button>
		</div>
	</dialog>
{/if}

<style>
	.stockline {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
	}
	.hint {
		color: var(--ink-faint);
		font-size: 0.85em;
		font-weight: normal;
	}
	h2 .hint {
		font-size: 0.8rem;
	}
	.warn {
		color: var(--warn, #b45309);
	}
	.bomrow {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.bomrow.add {
		margin-top: 8px;
	}
	.qty {
		width: 90px;
	}
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
	dialog.podialog li {
		margin-bottom: 6px;
	}
	dialog.podialog .price {
		width: 90px;
		margin: 0 6px;
		text-align: right;
	}
	dialog.podialog .actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
</style>
