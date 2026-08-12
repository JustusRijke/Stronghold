<script lang="ts">
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import type { StockValueReport } from '$lib/types';
	import { stockOrderLabel, stockOrderUrl } from '$lib/status';

	// Part prices are cached in the database and kept current by the writes that
	// change them, so the report just reads them -- no calculate step, no
	// client-side snapshot to go stale.
	let report = $state<StockValueReport | null>(null);

	$effect(() => {
		api.stockValue().then((r) => (report = r));
	});

	const BASIS_LABEL = {
		po: 'Purchase order',
		build: 'Build order',
		build_partial: 'Build order (partly estimated)',
		po_no_price: 'Purchase order (no price)',
		estimate: 'Estimate (latest PO)',
		virtual: 'Virtual component (labour)',
		none: 'No price known'
	};

	type Row = {
		item_id: number;
		sku: string;
		description: string;
		count: number;
		status: string;
		unit_price: number | null;
		value: number | null;
		basis: string;
		order: string;
		order_url: string;
		nonzero: boolean;
		last_po_date: string;
		part_active: boolean;
		part_assembly: boolean;
	};

	// the report names its PO columns basis_*; reshape so the shared stock
	// helpers can label and link the order the same way the stock tables do
	const origin = (r: StockValueReport['rows'][number]) => ({
		po_id: r.basis_po_id,
		po_reference: r.basis_po_reference,
		build_id: r.build_id,
		consumed_by_build_id: null,
		build_reference: r.build_reference
	});

	const rows = $derived<Row[]>(
		(report?.rows ?? []).map((r) => ({
			item_id: r.item_id,
			sku: r.sku,
			description: r.description,
			count: r.count,
			status: r.status,
			unit_price: r.unit_price,
			value: r.value,
			basis: BASIS_LABEL[r.basis],
			// build-priced stock has no source PO; it points at its build instead.
			// the report has no consumed_by_build_id, so a debt row shows no order.
			order: stockOrderLabel(origin(r)),
			order_url: stockOrderUrl(origin(r)),
			nonzero: r.count !== 0,
			last_po_date: r.last_po_date ?? '',
			part_active: r.part_active,
			part_assembly: r.part_assembly
		}))
	);

	// the rows surviving the table's filters, for the totals below
	let shown = $state<Row[]>([]);
	const shownTotal = $derived(shown.reduce((sum, r) => sum + (r.value ?? 0), 0));
	const shownUnpriced = $derived(shown.filter((r) => r.value === null).length);


	const money = (v: number | null) => (v === null ? '-' : v.toFixed(4));

	const columns: Column<Row>[] = [
		{ key: 'item_id', header: '#', width: '70px', mono: true, hideByDefault: true },
		{ key: 'sku', header: 'SKU', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'count', header: 'Count', width: '100px', mono: true },
		{
			key: 'unit_price',
			header: 'Unit price',
			width: '120px',
			mono: true,
			format: (v) => money(v as number | null)
		},
		{
			key: 'value',
			header: 'Value',
			width: '120px',
			mono: true,
			format: (v) => money(v as number | null),
			cellClass: (r) => (r.value === null ? 'muted' : '')
		},
		{
			key: 'basis',
			header: 'Priced by',
			width: '220px',
			statusFilter: true,
			statusOptions: Object.values(BASIS_LABEL)
		},
		{
			key: 'order',
			header: 'Order',
			width: '130px',
			mono: true,
			cellHref: (r) => r.order_url
		},
		// blank = never ordered; DataTable sorts blanks last in both directions,
		// so ascending shows the oldest real date first
		{ key: 'last_po_date', header: 'Last PO', width: '120px', mono: true },
		{
			key: 'nonzero',
			header: 'In stock',
			width: '90px',
			bool: true,
			boolPreset: true // settled debts and fully-consumed receipts sit at zero
		},
		{
			key: 'part_active',
			header: 'Active part',
			width: '110px',
			bool: true,
			boolPreset: true // inactive parts keep their leftover stock; hide it by default
		},
		{
			key: 'part_assembly',
			header: 'Assembly',
			width: '100px',
			bool: true // no preset: assemblies and bought parts both shown by default
		},
		{
			key: 'status',
			header: 'Stock status',
			width: '200px',
			statusFilter: true,
			statusOptions: ['Available', 'Consumed by build order'],
			statusDefaultHide: ['Consumed by build order']
		}
	];
</script>

<div class="content">
	<div class="head">
		<h1>Stock value</h1>
	</div>

	{#if report}
		<div class="summary">
			<div class="tile">
				<span class="label">Value shown</span>
				<span class="figure">{shownTotal.toFixed(2)}</span>
				<span class="sub">{shown.length} of {rows.length} items</span>
			</div>
			<div class="tile">
				<span class="label">Value of stock on hand</span>
				<span class="figure">{report.total.toFixed(2)}</span>
				<span class="sub">all parts, excludes consumed rows</span>
			</div>
			<div class="tile" class:warn={shownUnpriced > 0}>
				<span class="label">Without a price</span>
				<span class="figure">{shownUnpriced}</span>
				<span class="sub">{report.unpriced} of the stock on hand</span>
			</div>
			<div class="tile" class:warn={report.incomplete > 0}>
				<span class="label">Understated</span>
				<span class="figure">{report.incomplete}</span>
				<span class="sub">
					{#if report.incomplete > 0}
						{report.incomplete_value.toFixed(2)} counted; true value is higher
					{:else}
						every build fully costed
					{/if}
				</span>
			</div>
		</div>

		<DataTable
			{columns}
			{rows}
			bind:visibleRows={shown}
			href={(r) => `/stock/${r.item_id}`}
			storageKey="/reports/stock-value"
			defaultSort={{ key: 'value', dir: 'desc' }}
		/>
		<p class="note">
			Items priced by estimate use the latest purchase order price for that part. A line on a
			purchase order with no price filled in is reported as such, never valued at zero.
			"Build order (partly estimated)" means the build did not consume everything its
			components asked for, or an input was itself estimated -- that stock is worth at least
			the figure shown, and probably more. Filter "Priced by" to find them.
		</p>
	{:else}
		<p class="empty">Loading...</p>
	{/if}
</div>

<style>
	.content {
		padding: 16px 20px;
	}
	.head {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: 12px;
	}
	h1 {
		font-size: 1.2rem;
		margin: 0;
	}
	.summary {
		display: flex;
		gap: 10px;
		margin-bottom: 12px;
		flex-wrap: wrap;
	}
	.tile {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 11em;
		padding: 8px 14px;
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.tile.warn .figure {
		color: var(--warn, #b45309);
	}
	.label {
		color: var(--ink-soft);
		font-size: 0.78rem;
	}
	.figure {
		font-size: 1.3rem;
		font-variant-numeric: tabular-nums;
	}
	.sub {
		color: var(--ink-faint);
		font-size: 0.75rem;
	}
	.note,
	.empty {
		color: var(--ink-faint);
		font-size: 0.85rem;
	}
</style>
