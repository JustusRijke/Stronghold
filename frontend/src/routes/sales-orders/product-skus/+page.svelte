<script lang="ts">
	import { api } from '$lib/api';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import { toast } from '$lib/toast.svelte';
	import type { Part, PartSku } from '$lib/types';

	type Row = {
		id: number | null;
		sku: string;
		part: string;
		part_id: number | null;
		assembly: boolean;
		lines: number;
		state: string;
	};

	let skus = $state<PartSku[]>([]);
	let parts = $state<Part[]>([]);

	// the add row, open only while it is being filled in
	let adding = $state(false);
	let newSku = $state('');
	let newPart = $state<number | ''>('');

	const MAPPED = 'Mapped';
	const IGNORED = 'Ignored';
	const TODO = 'Not mapped';

	const rows = $derived<Row[]>(
		skus.map((s) => ({
			id: s.id,
			sku: s.sku,
			part: s.part_id ? `${s.part_sku ? s.part_sku + ' - ' : ''}${s.part_description}` : '',
			part_id: s.part_id,
			assembly: s.part_assembly,
			lines: s.lines,
			state: s.ignored ? IGNORED : s.part_id ? MAPPED : TODO
		}))
	);
	const todo = $derived(rows.filter((r) => r.state === TODO).length);

	async function load() {
		const [all, allParts] = await Promise.all([api.partSkus(), api.parts()]);
		skus = all;
		parts = allParts.filter((p) => p.active);
	}
	$effect(() => {
		load();
	});

	async function add(sku: string, partId: number) {
		if (await toast.run(() => api.addPartSku({ sku, part_id: partId }))) {
			adding = false;
			newSku = '';
			newPart = '';
			load();
		}
	}
	async function link(row: Row) {
		// an unmapped row already knows its sku; only the part is missing
		newSku = row.sku;
		newPart = '';
		adding = true;
	}
	async function ignore(sku: string) {
		if (await toast.run(() => api.ignoreSku({ sku }))) load();
	}
	async function remove(row: Row) {
		if (row.id && (await toast.run(() => api.removePartSku(row.id!)))) load();
	}

	const partLabel = (p: Part) => `${p.sku ? p.sku + ' - ' : ''}${p.description}`;

	const columns: Column<Row>[] = $derived([
		{ key: 'sku', header: 'Sold SKU', mono: true, width: '200px' },
		{
			key: 'part',
			header: 'Sold as',
			truncate: true,
			cellHref: (r) => (r.part_id ? `/parts/${r.part_id}` : '')
		},
		{ key: 'assembly', header: 'Assembly', bool: true, width: '100px' },
		{ key: 'lines', header: 'Sold', mono: true, width: '80px' },
		{
			key: 'state',
			header: 'State',
			width: '120px',
			statusFilter: true,
			statusOptions: [MAPPED, IGNORED, TODO]
		}
	]);
</script>

<div class="content nosidebar">
	<h1 class="h1">Product SKUs</h1>
	<p class="muted">
		What each sold SKU is. WooCommerce knows the SKU it sold, not what is behind it, so
		each one names a part here. A product made of several parts is an assembly &mdash; its
		BOM holds the list, and one sold unit consumes one of it off the shelf. Variants that
		are the same build (a door-left and a door-right) both name that one part, so the
		recipe is written once. A SKU that consumes nothing &mdash; shipping, a fee, a service
		&mdash; is ignored instead. Importing an order, or the &ldquo;Prefill from SKUs&rdquo;
		button on a sales order, does the filling in, and only ever touches a line that has no
		parts yet: changing a mapping never rewrites an order.
	</p>
	{#if todo}
		<p class="muted">
			<strong>{todo}</strong> sold SKU{todo === 1 ? '' : 's'} not mapped yet &mdash; filter
			the State column to find {todo === 1 ? 'it' : 'them'}.
		</p>
	{/if}

	{#if adding}
		<div class="addbar">
			<input list="soldskus" placeholder="Sold SKU" bind:value={newSku} />
			<datalist id="soldskus">
				<!-- the description goes in `label`, never in the option's text: a
				     datalist option's text content is what the browser inserts on pick,
				     so writing it there fills the box with the description instead of
				     the sku that is the key -->
				{#each rows.filter((r) => r.state === TODO) as r (r.sku)}
					<option value={r.sku} label={`${r.sku} (${r.lines} sold)`}></option>
				{/each}
			</datalist>
			<Picker
				bind:value={newPart}
				rows={parts}
				label={partLabel}
				id="newpartsku"
				onenter={() => newPart !== '' && add(newSku.trim(), Number(newPart))}
				wide
			/>
			<button
				class="btn"
				disabled={!newSku.trim() || newPart === ''}
				onclick={() => add(newSku.trim(), Number(newPart))}>Add</button
			>
			<button class="btn ghost" onclick={() => ignore(newSku.trim())} disabled={!newSku.trim()}
				>Ignore this SKU</button
			>
			<button class="btn ghost" onclick={() => (adding = false)}>Cancel</button>
		</div>
	{/if}

	<DataTable
		{columns}
		{rows}
		href={(r) => (r.part_id ? `/parts/${r.part_id}` : '')}
		rowKey={(r) => r.sku}
		storageKey="/sales-orders/product-skus"
		defaultSort={{ key: 'sku', dir: 'asc' }}
		onAdd={() => {
			newSku = '';
			newPart = '';
			adding = true;
		}}
		rowAction={{
			icon: '+',
			title: 'Map this SKU to a part',
			run: link,
			show: (r) => r.state === TODO
		}}
		onRemove={remove}
		canRemove={(r) => r.id !== null}
	/>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
	.muted {
		max-width: 70ch;
	}
	.addbar {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 10px;
	}
</style>
