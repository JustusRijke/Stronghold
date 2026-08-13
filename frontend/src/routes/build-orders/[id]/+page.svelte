<script lang="ts">
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { buildTabs } from '$lib/tabs.svelte';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import type { BuildLine, BuildOrder, StockItem } from '$lib/types';
	import { BUILD_STATUS_OPTIONS as BUILD_STATUS } from '$lib/status';
	import { STATUS_OPTIONS as STOCK_STATUS_OPTIONS } from '$lib/validators';

	// mirror the backend transition rules (db.edit_build): Complete only when
	// fully produced; once anything is produced, status is locked to Production
	// or Complete. Disabled options can't be picked but stay visible.
	function statusAllowed(st: string): boolean {
		if (!build) return true;
		if (st === 'Complete') return build.produced === build.quantity;
		if (build.produced > 0) return st === 'Production';
		return true;
	}

	const id = $derived(Number($page.params.id));

	let build = $state<BuildOrder | null>(null);
	let partName = $state('');
	// the build's own component snapshot, not the part's live BOM
	let bom = $state<BuildLine[]>([]);
	let stock = $state<StockItem[]>([]);
	let consumed = $state<StockItem[]>([]);
	let producedStock = $state<StockItem[]>([]);
	let notFound = $state(false);

	const sections = [
		{ id: 'details', label: 'Details' },
		{ id: 'components', label: 'Components' },
		{ id: 'consumed', label: 'Stock consumed' },
		{ id: 'produced', label: 'Stock produced' }
	];

	async function load() {
		notFound = false;
		try {
			build = await api.build(id);
		} catch {
			build = null;
			notFound = true;
			return;
		}
		buildTabs.open(id, build.reference || `Build ${id}`);
		const [part, lines, allStock, buildStock] = await Promise.all([
			api.part(build.part_id),
			api.buildLines(id),
			api.stock(),
			api.buildStock(id)
		]);
		partName = part.sku || part.description;
		bom = lines;
		stock = allStock;
		// inputs only: a row this build produced that a later build ate is also
		// Consumed, but it is output, not input
		consumed = buildStock.filter((s) => s.consumed_by_build_id === id);
		producedStock = buildStock;
	}

	// a finished build's components are history and never resync
	const canResync = $derived(
		!!build && build.status !== 'Complete' && build.status !== 'Cancelled'
	);
	async function resync() {
		if (await toast.run(() => api.resyncBuildLines(id))) load();
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	async function save(patch: Partial<BuildOrder>) {
		if (await toast.run(() => api.patchBuild(id, patch))) load();
	}
	// whole assemblies only, and never below what has already been produced
	function saveQuantity(input: HTMLInputElement) {
		const q = Number(input.value);
		if (!build) return;
		if (!Number.isInteger(q) || q < 1) {
			toast.show('Quantity must be a whole number', 'err');
			input.value = String(build.quantity);
			return;
		}
		if (q < build.produced) {
			toast.show(`Quantity cannot be below the ${build.produced} already produced`, 'err');
			input.value = String(build.quantity);
			return;
		}
		save({ quantity: q });
	}

	// available stock per part, summed client-side (only Available items)
	const available = $derived.by(() => {
		const m = new Map<number, number>();
		for (const s of stock)
			if (s.status === 'Available') m.set(s.part_id, (m.get(s.part_id) ?? 0) + s.count);
		return m;
	});
	// stock this build produced, whatever became of it: output later eaten by
	// another build is still this build's output, so status is not a filter here
	const produced = $derived(producedStock.filter((s) => s.build_id === id));
	const remaining = $derived(build ? build.quantity - build.produced : 0);
	// components short for producing `qty` units: {sku, required, have}. Producing is
	// never blocked -- the user sees the shortfall and decides whether to proceed
	// (the missing parts become a negative stock item owed by this build).
	function shortages(qty: number) {
		return bom
			.filter((l) => !l.component_virtual)
			.map((l) => ({
				part_id: l.component_part_id,
				sku: l.component_sku,
				description: l.component_description,
				required: l.quantity * qty,
				have: available.get(l.component_part_id) ?? 0
			}))
			.filter((c) => c.have < c.required);
	}

	// produce popup
	let dialog = $state<HTMLDialogElement | null>(null);
	let produceQty = $state(1);
	const dialogShortages = $derived(shortages(produceQty));
	function openProduce() {
		produceQty = remaining;
		dialog?.showModal();
	}
	async function doProduce() {
		if (await toast.run(() => api.produceBuild(id, produceQty))) {
			dialog?.close();
			load();
		}
	}

	const stockCols: Column<StockItem>[] = [
		{ key: 'id', header: '#', mono: true, width: '80px' },
		{ key: 'sku', header: 'SKU', mono: true, width: '140px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'count', header: 'Count', mono: true, width: '100px' },
		{
			key: 'status',
			header: 'Status',
			width: '160px',
			statusFilter: true,
			statusOptions: [...STOCK_STATUS_OPTIONS]
		}
	];

	// component rows for the DataTable: BOM lines enriched with required/have.
	// Required covers the units still to produce (remaining), not the whole build.
	// virtual = unlimited, never short.
	type CompRow = {
		component_part_id: number;
		sku: string;
		description: string;
		required: number;
		have: number;
		consumed: number;
		virtual: boolean;
		short: boolean;
	};
	const compRows = $derived.by<CompRow[]>(() =>
		bom.map((l) => {
			const required = l.quantity * remaining;
			const have = available.get(l.component_part_id) ?? 0;
			return {
				component_part_id: l.component_part_id,
				sku: l.component_sku,
				description: l.component_description,
				required,
				have,
				consumed: l.consumed,
				virtual: l.component_virtual,
				short: !l.component_virtual && have < required
			};
		})
	);
	const compCols: Column<CompRow>[] = [
		{ key: 'sku', header: 'SKU', mono: true, width: '140px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'required', header: 'Required', mono: true, width: '100px' },
		{ key: 'consumed', header: 'Consumed', mono: true, width: '100px' },
		{
			key: 'have',
			header: 'Have',
			mono: true,
			width: '100px',
			format: (_v, r) => (r.virtual ? '∞' : r.have),
			cellClass: (r) => (r.short ? 'short' : '')
		}
	];

	const consumedCols: Column<StockItem>[] = [
		{ key: 'id', header: '#', mono: true, width: '80px' },
		{ key: 'sku', header: 'SKU', mono: true, width: '140px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'count', header: 'Count', mono: true, width: '100px' },
		{
			key: 'unit_price',
			header: 'Unit price',
			mono: true,
			width: '110px',
			format: (v) => (v == null ? '--' : Number(v).toFixed(4))
		},
		{
			key: 'po_reference',
			header: 'From PO',
			mono: true,
			width: '130px',
			// virtual components (labour) are never purchased, so they have no
			// source order -- say so rather than showing a bare dash
			format: (v, r) => (v ? String(v) : r.price_basis === 'virtual' ? 'virtual' : '--')
		}
	];
	const consumedTotal = $derived(
		consumed.reduce((t, s) => t + (s.unit_price ?? 0) * s.count, 0)
	);
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No build order {id}.</p></div>
{:else if build}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">Build {build.reference || build.id}</h1>
				<span class="badge">{build.status}</span>
			</div>
			<p>
				Assembly: <a href={`/parts/${build.part_id}`}>{partName}</a>
				<span class="muted"
					>&middot; produced {build.produced} of {build.quantity}</span
				>
			</p>

			<section id="details">
				<label class="field">
					<span>Description</span>
					<input
						value={build.description}
						onblur={(e) => save({ description: e.currentTarget.value })}
					/>
				</label>
				<label class="field">
					<span>Reference</span>
					<input value={build.reference} onblur={(e) => save({ reference: e.currentTarget.value })} />
				</label>
				<label class="field">
					<span>Quantity</span>
					<input
						type="number"
						min={Math.max(1, build.produced)}
						step="1"
						value={build.quantity}
						onblur={(e) => saveQuantity(e.currentTarget)}
					/>
				</label>
				<label class="field">
					<span>Status</span>
					<select value={build.status} onchange={(e) => save({ status: e.currentTarget.value })}>
						{#if build.status && !BUILD_STATUS.includes(build.status)}
							<option value={build.status}>{build.status}</option>
						{/if}
						{#each BUILD_STATUS as st (st)}
							<option value={st} disabled={st !== build.status && !statusAllowed(st)}>{st}</option>
						{/each}
					</select>
				</label>
				<label class="field">
					<span>Start date</span>
					<input
						type="date"
						value={build.start_date ?? ''}
						onchange={(e) => save({ start_date: e.currentTarget.value || null })}
					/>
				</label>
				<label class="field">
					<span>Target date</span>
					<input
						type="date"
						value={build.end_date ?? ''}
						onchange={(e) => save({ end_date: e.currentTarget.value || null })}
					/>
				</label>
			</section>

			<section id="components">
				<div class="head">
					<h2 class="h2">Components</h2>
					{#if build.status === 'Production' && remaining > 0}
						<button class="btn" onclick={openProduce}>Produce stock</button>
					{/if}
				</div>
				<p class="muted">
					The components this build was created against &mdash; a later change to the
					assembly's BOM does not alter it.
				</p>
				{#if build.bom_drifted}
					<div class="drift">
						<span>
							⚠ The assembly's BOM has changed since this build was created.
							{#if !canResync}
								This build is {build.status}, so its components stay as they were.
							{/if}
						</span>
						{#if canResync}
							<button class="btn ghost" onclick={resync}>Update to current BOM</button>
						{/if}
					</div>
				{/if}
				{#if bom.length === 0}
					<p class="muted">This build has no component lines; nothing to build.</p>
				{:else}
					<DataTable
						columns={compCols}
						rows={compRows}
						href={(r) => `/parts/${r.component_part_id}`}
						storageKey={`/build-orders/${id}/components`}
						defaultSort={{ key: 'description', dir: 'asc' }}
					/>
				{/if}
			</section>

			<section id="consumed">
				<h2 class="h2">Stock consumed ({consumed.length})</h2>
				{#if consumed.length === 0}
					<p class="muted">This build has not consumed any stock yet.</p>
				{:else}
					<p class="muted">
						What this build actually consumed, at the price it was bought for &mdash; this is
						what prices the output. Virtual components (labour) are listed at the rate
						this build recorded; they hold no stock, so nothing is drawn down.
						Total {consumedTotal.toFixed(2)}.
					</p>
					<DataTable
						columns={consumedCols}
						rows={consumed}
						href={(s) => `/stock/${s.id}`}
						storageKey={`/build-orders/${id}/consumed`}
					/>
				{/if}
			</section>

			<section id="produced">
				<h2 class="h2">Stock produced ({produced.length})</h2>
				<DataTable
					columns={stockCols}
					rows={produced}
					href={(s) => `/stock/${s.id}`}
					storageKey={`/build-orders/${id}/stock`}
				/>
			</section>
		</div>
	</div>

	<dialog bind:this={dialog} class="produce">
		<h2 class="h2">Produce stock?</h2>
		<p class="muted">Consumes components (FIFO) for each unit produced.</p>
		<label class="field">
			<span>Quantity</span>
			<input type="number" min="1" step="1" max={remaining} bind:value={produceQty} />
		</label>
		{#if dialogShortages.length > 0}
			<div class="warn">
				<p class="short"><strong>Short on {dialogShortages.length} component(s):</strong></p>
				<ul>
					{#each dialogShortages as c (c.part_id)}
						<li>
							{#if c.sku}<span class="mono">{c.sku}</span> &mdash; {/if}{c.description}: requires
							{c.required}, have {c.have}
						</li>
					{/each}
				</ul>
				<p class="muted">
					You can produce anyway -- the full quantity is still produced and the missing
					parts are recorded as negative stock, valued at the part's estimated price
					and deducted from stock value. Receiving them on a purchase order clears the
					shortfall and reprices this build at what you actually paid.
				</p>
			</div>
		{/if}
		<div class="actions">
			<button class="btn ghost" onclick={() => dialog?.close()}>Cancel</button>
			<button class="btn" onclick={doProduce} disabled={produceQty <= 0 || produceQty > remaining}>
				Produce
			</button>
		</div>
	</dialog>
{/if}

<style>
	dialog.produce {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 20px;
		min-width: 320px;
		background: var(--card);
		color: var(--ink);
	}
	dialog.produce::backdrop {
		background: rgba(0, 0, 0, 0.4);
	}
	dialog.produce .actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
	.warn {
		margin-top: 12px;
	}
	.warn ul {
		margin: 4px 0 8px 20px;
		font-size: 0.9em;
	}
	.short {
		color: var(--bad);
	}
	.drift {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		flex-wrap: wrap;
		margin: 8px 0 12px;
		padding: 8px 12px;
		border: 1px solid var(--line);
		border-radius: 6px;
	}
</style>

