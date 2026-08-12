<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';

	type Row = {
		id: number;
		supplier: string;
		sku: string;
		part_sku: string;
		description: string;
		ean: string;
		pack_qty: number;
		hyperlink: string;
		active: boolean;
	};
	let rows = $state<Row[]>([]);

	async function load() {
		const suppliers = new Map((await api.suppliers()).map((s) => [s.id, s.name]));
		rows = (await api.supplierParts()).map((p) => ({
			id: p.id,
			supplier: suppliers.get(p.supplier_id) ?? String(p.supplier_id),
			sku: p.sku,
			part_sku: p.part_sku,
			description: p.description,
			ean: p.ean,
			pack_qty: p.pack_qty,
			hyperlink: p.hyperlink,
			active: p.active
		}));
	}
	$effect(() => {
		load();
	});

	const columns: Column<Row>[] = [
		{ key: 'supplier', header: 'Supplier', width: '160px' },
		{ key: 'sku', header: 'SKU', mono: true, width: '140px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'part_sku', header: 'Part', width: '140px' },
		{ key: 'ean', header: 'EAN', truncate: true },
		{ key: 'pack_qty', header: 'Pack', width: '80px', mono: true },
		{ key: 'hyperlink', header: 'URL', truncate: true },
		{ key: 'active', header: 'Active', bool: true, boolPreset: true, width: '100px' }
	];
</script>

<div class="content nosidebar">
	<DataTable
		{columns}
		{rows}
		href={(r) => `/supplier-parts/${r.id}`}
		storageKey="/supplier-parts"
		onAdd={() => goto('/supplier-parts/new')}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
