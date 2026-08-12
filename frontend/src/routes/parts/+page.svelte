<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import type { Part } from '$lib/types';

	let rows = $state<Part[]>([]);

	async function load() {
		rows = await api.parts();
	}
	$effect(() => {
		load();
	});

	const columns: Column<Part>[] = [
		{ key: 'id', header: '#', width: '70px', mono: true, hideByDefault: true },
		{ key: 'sku', header: 'SKU', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'in_stock', header: 'In stock', mono: true, width: '100px' },
		{ key: 'needed', header: 'Needed', mono: true, width: '100px' },
		{ key: 'incoming', header: 'On order', mono: true, width: '100px' },
		{ key: 'suggested_order', header: 'To order', mono: true, width: '100px' },
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
