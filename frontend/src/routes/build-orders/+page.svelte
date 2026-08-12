<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import { BUILD_STATUS_OPTIONS } from '$lib/status';

	// builds in a finished state are hidden by default (the "Open" filter)
	const DONE = ['Complete', 'Cancelled'];

	type Row = {
		id: number;
		reference: string;
		part: string;
		quantity: number;
		produced: number;
		status: string;
		end_date: string;
	};
	let rows = $state<Row[]>([]);

	async function load() {
		const parts = new Map((await api.parts()).map((p) => [p.id, p.sku || p.description]));
		rows = (await api.builds()).map((b) => ({
			id: b.id,
			reference: b.reference,
			part: parts.get(b.part_id) ?? String(b.part_id),
			quantity: b.quantity,
			produced: b.produced,
			status: b.status,
			end_date: b.end_date ?? ''
		}));
	}
	$effect(() => {
		load();
	});

	const columns: Column<Row>[] = [
		{ key: 'reference', header: 'Build', mono: true, width: '160px' },
		{ key: 'part', header: 'Assembly', width: '180px', truncate: true },
		{ key: 'quantity', header: 'Qty', mono: true, width: '90px' },
		{ key: 'produced', header: 'Produced', mono: true, width: '100px' },
		{
			key: 'status',
			header: 'Status',
			width: '120px',
			statusFilter: true,
			statusOptions: BUILD_STATUS_OPTIONS,
			statusDefaultHide: DONE
		},
		{ key: 'end_date', header: 'Target', width: '140px' }
	];
</script>

<div class="content nosidebar">
	<DataTable
		{columns}
		{rows}
		href={(r) => `/build-orders/${r.id}`}
		storageKey="/build-orders"
		defaultSort={{ key: 'end_date', dir: 'desc' }}
		onAdd={() => goto('/build-orders/new')}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
