<script lang="ts">
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import {
		stockConsumerUrl,
		stockOrderLabel,
		stockOrderUrl,
		STOCK_CONSUMED,
		stockStatusLabel,
		STOCK_STATUS_OPTIONS,
		CREATED_COLUMN
	} from '$lib/status';

	type Row = {
		id: number;
		sku: string;
		description: string;
		count: number;
		created_at: string;
		order: string;
		order_url: string;
		consumed_by: string;
		consumed_by_url: string;
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
			created_at: i.created_at,
			order: stockOrderLabel(i),
			order_url: stockOrderUrl(i),
			consumed_by: i.consumed_by_reference,
			consumed_by_url: stockConsumerUrl(i),
			nonzero: i.count !== 0,
			status: i.status
		}));
	}
	$effect(() => {
		load();
	});

	const columns: Column<Row>[] = [
		{ key: 'id', header: '#', width: '70px', mono: true, hideByDefault: true },
		CREATED_COLUMN,
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
			// blank for stock still on the shelf; the consumed rows are hidden by
			// default, so this column is mostly empty until you unhide them
			key: 'consumed_by',
			header: 'Consumed by',
			width: '130px',
			mono: true,
			cellHref: (r) => r.consumed_by_url
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
			statusOptions: STOCK_STATUS_OPTIONS,
			statusDefaultHide: [STOCK_CONSUMED],
			statusLabel: stockStatusLabel,
			format: (v) => stockStatusLabel(String(v))
		}
	];
</script>

<div class="content nosidebar">
	<!-- no add button: stock items come from receiving a PO, a build, or a
	     stocktake on the part page -- never by hand -->
	<DataTable
		{columns}
		{rows}
		href={(r) => `/stock/${r.id}`}
		defaultSort={{ key: 'created_at', dir: 'desc' }}
		storageKey="/stock-v2"
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
