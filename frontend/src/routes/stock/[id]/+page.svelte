<script lang="ts">
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { stockTabs } from '$lib/tabs.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import type { StockItem } from '$lib/types';

	const id = $derived(Number($page.params.id));
	let item = $state<StockItem | null>(null);
	let notFound = $state(false);

	const sections = [{ id: 'details', label: 'Details' }];

	const PRICE_BASIS: Record<StockItem['price_basis'], string> = {
		po: 'from its purchase order',
		build: 'from what its build order consumed',
		build_partial: 'from its build order, partly estimated',
		estimate: 'estimated from the part price',
		virtual: 'a virtual component, at the rate its build recorded',
		po_no_price: 'its purchase order has no price',
		none: 'never purchased'
	};

	async function load() {
		notFound = false;
		try {
			item = await api.stockItem(id);
		} catch {
			item = null;
			notFound = true;
			return;
		}
		stockTabs.open(id, `#${item.id}${item.sku ? ` - ${item.sku}` : ''}`);
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	async function saveCount(v: number) {
		if (await toast.run(() => api.patchStock(id, { count: v }))) load();
	}
	function label(i: StockItem) {
		return i.sku ? `${i.sku} - ${i.description}` : i.description;
	}
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No stock item with id {id}.</p></div>
{:else if item}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">Stock item {item.id}</h1>
				<span class="badge">{item.status}</span>
			</div>

			<section id="details">
				<p>Part: <a class="mono" href={`/parts/${item.part_id}`}>{label(item)}</a></p>
				{#if item.po_id}
					<p>
						Purchase order:
						<a class="mono" href={`/purchase-orders/${item.po_id}`}
							>{item.po_reference || item.po_id}</a
						>
					</p>
				{/if}
				{#if item.build_id}
					<p>
						Built by:
						<a class="mono" href={`/build-orders/${item.build_id}`}
							>{item.build_reference || item.build_id}</a
						>
					</p>
				{/if}
				{#if item.consumed_by_build_id}
					<p>
						Consumed by:
						<a class="mono" href={`/build-orders/${item.consumed_by_build_id}`}
							>{item.consumed_by_reference || item.consumed_by_build_id}</a
						>
					</p>
				{/if}
				<p>Status: {item.status}</p>
				<p>
					Value:
					{#if item.unit_price === null}
						<strong>unknown</strong>
						<span class="hint">{PRICE_BASIS[item.price_basis]}</span>
					{:else}
						<strong>{(item.unit_price * item.count).toFixed(2)}</strong>
						<span class="hint">
							{item.count} &times; {item.unit_price.toFixed(4)} &mdash;
							{PRICE_BASIS[item.price_basis]}
						</span>
					{/if}
				</p>
				<label class="field">
					<span>Count</span>
					<input
						type="number"
						min="0"
						step="any"
						value={item.count}
						onblur={(e) => saveCount(Number(e.currentTarget.value))}
					/>
				</label>
			</section>
		</div>
	</div>
{/if}

<style>
	.hint {
		color: var(--ink-faint);
		font-size: 0.85em;
	}
</style>
