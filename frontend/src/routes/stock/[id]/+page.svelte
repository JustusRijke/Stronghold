<script lang="ts">
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import { stockTabs } from '$lib/tabs.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import SettleDebtDialog from '$lib/components/SettleDebtDialog.svelte';
	import { expert } from '$lib/expert.svelte';
	import { toast } from '$lib/toast.svelte';
	import type { StockItem } from '$lib/types';

	const id = $derived(Number($page.params.id));
	let item = $state<StockItem | null>(null);
	let notFound = $state(false);
	let settling = $state(false);
	// a row belonging to a build's shortfall rather than to the shelf. Still
	// true once it settles to zero, so an expert edit cannot turn it into stock
	const owedToBuild = $derived(
		!!item && item.status === 'Available' && !!item.consumed_by_build_id
	);
	// an outstanding shortfall: one still owing stock, so still settleable
	const isDebt = $derived(owedToBuild && !!item && item.count < 0);

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

	async function setCount(count: number) {
		if (count === item?.count) return;
		await toast.run(async () => (item = await api.patchStock(id, { count })), 'Saved');
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

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
				{#if isDebt}
					<button class="btn" type="button" onclick={() => (settling = true)}>
						Settle from stock
					</button>
				{/if}
			</div>

			<section id="details">
				<p>Part: <a class="mono" href={`/parts/${item.part_id}`}>{label(item)}</a></p>
				{#if item.po_id}
					<p>
						Purchase order:
						<a class="mono" href={`/purchase-orders/${item.po_id}`}
							>{item.po_reference}</a
						>
					</p>
				{/if}
				{#if item.build_id}
					<p>
						Built by:
						<a class="mono" href={`/build-orders/${item.build_id}`}
							>{item.build_reference}</a
						>
					</p>
				{/if}
				{#if item.consumed_by_build_id}
					<p>
						Consumed by:
						<a class="mono" href={`/build-orders/${item.consumed_by_build_id}`}
							>{item.consumed_by_reference}</a
						>
					</p>
				{/if}
				{#if item.stocktake_reason}
					<p>
						Stocktake: {item.stocktake_reason}
						{#if item.stocktake_at}
							<span class="hint">{new Date(item.stocktake_at).toLocaleString()}</span>
						{/if}
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
				{#if expert.on}
					<label class="field">
						<span>Count</span>
						<!-- a row keeps its sign: shelf stock stays >= 0, a debt row stays
						     <= 0. The backend enforces the same, this just says so first -->
						<input
							type="number"
							step="any"
							min={owedToBuild ? undefined : 0}
							max={owedToBuild ? 0 : undefined}
							value={item.count}
							onblur={(e) => setCount(Number(e.currentTarget.value))}
						/>
						{#if owedToBuild}
							<span class="hint">stock owed to a build; settle it to 0, never above</span>
						{/if}
					</label>
				{:else}
					<p>
						Count: <strong>{item.count}</strong>
						<span class="hint">
							corrected with Stocktake on the <a href={`/parts/${item.part_id}`}>part page</a>
						</span>
					</p>
				{/if}
			</section>
		</div>
	</div>
	{#if isDebt}
		<SettleDebtDialog debt={item} bind:open={settling} onsaved={load} />
	{/if}
{/if}

<style>
	.hint {
		color: var(--ink-faint);
		font-size: 0.85em;
	}
</style>
