<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import { PO_STATUS_OPTIONS } from '$lib/status';

	// orders in a finished state are hidden by default (the "Open" filter)
	const DONE = ['Complete', 'Cancelled', 'Lost', 'Returned'];

	type Row = {
		id: number;
		reference: string;
		description: string;
		supplier: string;
		status: string;
		total: number;
		end_date: string;
	};
	let rows = $state<Row[]>([]);

	async function load() {
		const suppliers = new Map((await api.suppliers()).map((s) => [s.id, s.name]));
		rows = (await api.pos()).map((po) => ({
			id: po.id,
			reference: po.reference,
			description: po.description,
			supplier: suppliers.get(po.supplier_id) ?? String(po.supplier_id),
			status: po.status,
			total: po.total,
			end_date: po.end_date ?? ''
		}));
	}
	$effect(() => {
		load();
	});

	const columns: Column<Row>[] = [
		{ key: 'reference', header: 'PO', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'supplier', header: 'Supplier', width: '160px', truncate: true },
		{
			key: 'total', // goods + delivery
			header: 'Total',
			mono: true,
			width: '110px',
			format: (v) => (v as number).toFixed(2)
		},
		{
			key: 'status',
			header: 'Status',
			width: '120px',
			statusFilter: true,
			statusOptions: PO_STATUS_OPTIONS,
			statusDefaultHide: DONE
		},
		{ key: 'end_date', header: 'Target', width: '150px' }
	];
</script>

<div class="content nosidebar">
	<DataTable
		{columns}
		{rows}
		href={(r) => `/purchase-orders/${r.id}`}
		storageKey="/purchase-orders"
		defaultSort={{ key: 'end_date', dir: 'desc' }}
		onAdd={() => goto('/purchase-orders/new')}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
