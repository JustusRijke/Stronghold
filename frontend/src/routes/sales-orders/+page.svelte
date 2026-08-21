<script lang="ts">
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import { SO_DONE, SO_STATUS_OPTIONS, soStatusLabel } from '$lib/status';
	import { toast } from '$lib/toast.svelte';

	type Row = {
		id: number;
		reference: string;
		wc_number: string;
		date_created: string;
		customer_name: string;
		shipping_country: string;
		status: string;
		revenue: number;
		margin: number | null;
		booked: boolean;
	};
	let rows = $state<Row[]>([]);

	// default window: the last 7 days, the usual "what came in this week" run
	const iso = (d: Date) => d.toISOString().slice(0, 10);
	const weekAgo = new Date(Date.now() - 7 * 24 * 3600 * 1000);
	let after = $state(iso(weekAgo));
	let before = $state('');
	let importing = $state(false);

	async function load() {
		rows = (await api.salesOrders()).map((so) => ({
			id: so.id,
			reference: so.reference,
			wc_number: so.wc_number,
			date_created: so.date_created ?? '',
			customer_name: so.customer_name,
			shipping_country: so.shipping_country,
			status: so.status,
			revenue: so.revenue,
			// realised once booked, else the estimate -- whichever we actually know
			margin: so.realised_margin_pct ?? so.estimated_margin_pct,
			booked: so.booked
		}));
	}
	$effect(() => {
		load();
	});

	async function runImport() {
		importing = true;
		try {
			await toast.run(async () => {
				const r = await api.importSalesOrders({ after, before: before || null });
				await load();
				toast.show(
					`Imported ${r.imported} new, updated ${r.updated}, skipped ${r.skipped}` +
						(r.notes.length ? ` (${r.notes.length} note(s))` : '')
				);
			});
		} finally {
			importing = false;
		}
	}

	const money = (v: number | null) => (v === null ? '' : v.toFixed(2));

	const columns: Column<Row>[] = [
		{ key: 'reference', header: 'Sale', mono: true, width: '110px' },
		{ key: 'wc_number', header: 'WooCommerce', mono: true, width: '130px' },
		{ key: 'date_created', header: 'Date', width: '120px' },
		{ key: 'customer_name', header: 'Customer', truncate: true },
		{ key: 'shipping_country', header: 'Country', width: '90px' },
		{
			key: 'status',
			header: 'Status',
			width: '120px',
			statusFilter: true,
			statusOptions: SO_STATUS_OPTIONS,
			statusDefaultHide: SO_DONE,
			statusLabel: soStatusLabel
		},
		{ key: 'revenue', header: 'Revenue', mono: true, width: '110px', format: (v) => money(v as number) },
		{
			key: 'margin',
			header: 'Margin',
			mono: true,
			width: '100px',
			format: (v) => (v === null ? '' : `${(v as number).toFixed(1)}%`)
		},
		{ key: 'booked', header: 'Booked', bool: true, width: '90px' }
	];
</script>

<div class="content nosidebar">
	<div class="importbar">
		<label>From <input type="date" bind:value={after} /></label>
		<label>To <input type="date" bind:value={before} /></label>
		<button onclick={runImport} disabled={importing}>
			{importing ? 'Importing...' : 'Import from WooCommerce'}
		</button>
	</div>
	<DataTable
		{columns}
		{rows}
		href={(r) => `/sales-orders/${r.id}`}
		storageKey="/sales-orders"
		defaultSort={{ key: 'date_created', dir: 'desc' }}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
	.importbar {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: 12px;
	}
	.importbar label {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 13px;
	}
</style>
