<script lang="ts">
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import type { InactivePartSuspectRow } from '$lib/types';

	let rows = $state<InactivePartSuspectRow[] | null>(null);

	$effect(() => {
		api.inactivePartSuspects().then((r) => (rows = r));
	});

	const qty = (v: unknown) => (v as number).toFixed(2).replace(/\.00$/, '');
	const used = (v: unknown) =>
		(v as number) === 0 ? 'no BOM' : `${v} inactive assembl${v === 1 ? 'y' : 'ies'}`;

	const columns: Column<InactivePartSuspectRow>[] = [
		{ key: 'sku', header: 'SKU', mono: true, width: '160px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'inactive_parents', header: 'Used in', width: '170px', format: used },
		{ key: 'in_stock', header: 'In stock', width: '110px', mono: true, format: qty },
		{ key: 'assembly', header: 'Assembly', width: '100px', bool: true },
		{ key: 'part_virtual', header: 'Virtual', width: '90px', bool: true }
	];
</script>

<div class="content">
	<h1>Suspected inactive parts</h1>

	{#if rows}
		<DataTable
			{columns}
			{rows}
			href={(r) => `/parts/${r.part_id}`}
			storageKey="/reports/inactive-part-suspects"
			defaultSort={{ key: 'description', dir: 'asc' }}
		/>
		<p class="note">
			Active parts that nothing live appears to use: either they are in no bill of materials at
			all, or every assembly they are still a component of has itself been deactivated. Neither
			a sold product sku nor a sales order line maps to them. Stock on hand is shown but does
			not disqualify a part -- that is exactly the case worth deciding about. This is a
			suggestion list: nothing is deactivated here, open the part to do that.
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
