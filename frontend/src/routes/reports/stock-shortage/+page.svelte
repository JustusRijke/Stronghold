<script lang="ts">
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import type { StockShortageRow } from '$lib/types';

	let rows = $state<StockShortageRow[] | null>(null);

	$effect(() => {
		api.stockShortage().then((r) => (rows = r));
	});

	const qty = (v: unknown) => (v as number).toFixed(2).replace(/\.00$/, '');

	const columns: Column<StockShortageRow>[] = [
		{ key: 'sku', header: 'SKU', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'shortage', header: 'Balance', width: '110px', mono: true, format: qty },
		{ key: 'in_stock', header: 'In stock', width: '110px', mono: true, format: qty },
		{ key: 'needed', header: 'Needed for sales', width: '150px', mono: true, format: qty },
		{
			key: 'part_virtual',
			header: 'Virtual',
			width: '90px',
			bool: true // labour and the like: never stocked, so always short
		}
	];
</script>

<div class="content">
	<h1>Stock shortage</h1>

	{#if rows}
		<DataTable
			{columns}
			rows={rows}
			href={(r) => `/parts/${r.part_id}`}
			storageKey="/reports/stock-shortage"
			defaultSort={{ key: 'shortage', dir: 'asc' }}
		/>
		<p class="note">
			Every part's stock position against the open sales orders: negative means short,
			positive is what is left over. An assembly that is short is exploded
			through its bill of materials as if it were built -- only the shortfall, since the
			units already on the shelf cover that many sales -- so only the parts you can actually
			buy are listed. "Needed for sales" therefore includes what those assemblies pull
			through. Build orders are ignored entirely: this is what the sales on the books ask
			for, whatever has already been planned to build.
		</p>
	{:else}
		<p class="note">Loading...</p>
	{/if}
</div>

<style>
	.content {
		padding: 16px 20px;
	}
	h1 {
		font-size: 1.2rem;
		margin: 0 0 12px;
	}
	.note {
		color: var(--ink-faint);
		font-size: 0.85rem;
		max-width: 60em;
	}
</style>
