<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import type { Supplier } from '$lib/types';

	let rows = $state<Supplier[]>([]);

	async function load() {
		rows = await api.suppliers();
	}
	$effect(() => {
		load();
	});

	const columns: Column<Supplier>[] = [
		{ key: 'id', header: 'ID', mono: true, width: '100px' },
		{ key: 'name', header: 'Name', truncate: true },
		{ key: 'active', header: 'Active', bool: true, boolPreset: true, width: '100px' }
	];
</script>

<div class="content nosidebar">
	<DataTable
		{columns}
		{rows}
		href={(r) => `/suppliers/${r.id}`}
		storageKey="/suppliers"
		onAdd={() => goto('/suppliers/new')}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
