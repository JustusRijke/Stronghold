<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import { STATUS_OPTIONS } from '$lib/validators';
	import { stockOrderLabel, stockOrderUrl } from '$lib/status';

	type Row = {
		id: number;
		sku: string;
		description: string;
		count: number;
		order: string;
		order_url: string;
		nonzero: boolean;
		status: string;
	};
	let rows = $state<Row[]>([]);

	async function load() {
		rows = (await api.stock()).map((i) => ({
			id: i.id,
			sku: i.sku,
			description: i.description,
			count: i.count,
			order: stockOrderLabel(i),
			order_url: stockOrderUrl(i),
			nonzero: i.count !== 0,
			status: i.status
		}));
	}
	$effect(() => {
		load();
	});

	const columns: Column<Row>[] = [
		{ key: 'id', header: '#', width: '70px', mono: true, hideByDefault: true },
		{ key: 'sku', header: 'SKU', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'count', header: 'Count', width: '110px', mono: true },
		{
			key: 'order',
			header: 'Order',
			width: '130px',
			mono: true,
			cellHref: (r) => r.order_url
		},
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
			width: '200px',
			statusFilter: true,
			statusOptions: [...STATUS_OPTIONS],
			statusDefaultHide: ['Consumed by build order']
		}
	];
</script>

<div class="content nosidebar">
	<DataTable
		{columns}
		{rows}
		href={(r) => `/stock/${r.id}`}
		storageKey="/stock"
		onAdd={() => goto('/stock/new')}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
