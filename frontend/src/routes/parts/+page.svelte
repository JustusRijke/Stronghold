<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import type { Part } from '$lib/types';

	type Row = Part & { stock: number };
	let rows = $state<Row[]>([]);

	async function load() {
		const [parts, stock] = await Promise.all([api.parts(), api.stock()]);
		const totals = new Map<number, number>();
		for (const s of stock)
			if (s.status === 'Available')
				totals.set(s.part_id, (totals.get(s.part_id) ?? 0) + s.count);
		rows = parts.map((p) => ({ ...p, stock: totals.get(p.id) ?? 0 }));
	}
	$effect(() => {
		load();
	});

	const columns: Column<Row>[] = [
		{ key: 'id', header: '#', width: '70px', mono: true, hideByDefault: true },
		{ key: 'sku', header: 'SKU', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'stock', header: 'In stock', mono: true, width: '100px' },
		{
			key: 'estimated_price',
			header: 'Est. price',
			mono: true,
			width: '110px',
			format: (v, r) => (v === null ? '-' : `${(v as number).toFixed(4)}${r.price_partial ? '*' : ''}`)
		},
		{ key: 'assembly', header: 'Assembly', bool: true, boolPreset: null, width: '110px' },
		{ key: 'virtual', header: 'Virtual', bool: true, boolPreset: null, width: '100px' },
		{ key: 'active', header: 'Active', bool: true, boolPreset: true, width: '100px' }
	];
</script>

<div class="content nosidebar">
	<DataTable
		{columns}
		{rows}
		href={(r) => `/parts/${r.id}`}
		storageKey="/parts"
		onAdd={() => goto('/parts/new')}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
