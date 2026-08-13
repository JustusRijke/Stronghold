<script lang="ts">
	// Pay off a build shortfall (a negative stock item) out of stock already on
	// the shelf. The same thing receiving the parts on a purchase order does,
	// for when the stock is already there.
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import type { StockItem } from '$lib/types';

	interface Props {
		debt: StockItem;
		open: boolean;
		onsaved: () => void;
	}
	let { debt, open = $bindable(), onsaved }: Props = $props();

	let dialog = $state<HTMLDialogElement | null>(null);
	let quantity = $state(0);
	let sourceId = $state<number | ''>('');
	let auto = $state(true);
	let items = $state<StockItem[]>([]);

	const owedQty = $derived(-debt.count);
	// oldest first, matching the default the backend picks
	const sources = $derived(
		items
			.filter((i) => i.part_id === debt.part_id && i.status === 'Available' && i.count > 0)
			.sort((a, b) => a.id - b.id)
	);
	const available = $derived(sources.reduce((t, i) => t + i.count, 0));
	const source = $derived(sources.find((i) => i.id === Number(sourceId)));
	// automatic spills across items FIFO; a chosen item is the only source
	const tooMuch = $derived(auto ? quantity > available : !!source && quantity > source.count);

	$effect(() => {
		if (open) {
			auto = true;
			dialog?.showModal();
			api.stock().then((all) => {
				items = all;
				sourceId = sources[0]?.id ?? '';
				quantity = Math.min(owedQty, available);
			});
		} else {
			dialog?.close();
		}
	});

	async function save() {
		const ok = await toast.run(
			() =>
				api.settleDebt(debt.id, {
					quantity,
					// null lets the backend walk the shelf FIFO
					item_id: auto || sourceId === '' ? null : Number(sourceId)
				}),
			'Shortfall settled'
		);
		if (ok) {
			open = false;
			onsaved();
		}
	}
</script>

<dialog bind:this={dialog} class="settle" onclose={() => (open = false)}>
	<h2 class="h2">Settle from stock</h2>
	<p class="muted">
		{debt.description} &middot; <strong>{owedQty}</strong> owed to
		{debt.consumed_by_reference || `BO-${debt.consumed_by_build_id}`}, {available} in stock
	</p>

	<label class="field req">
		<span>Quantity to settle</span>
		<input type="number" min="0" max={owedQty} step="any" bind:value={quantity} />
	</label>

	<label class="field">
		<span>Take from</span>
		<label class="check auto">
			<input type="checkbox" bind:checked={auto} />
			Automatic (oldest stock first, across items)
		</label>
		<select bind:value={sourceId} disabled={auto}>
			{#each sources as i (i.id)}
				<option value={i.id}>
					#{i.id} &middot; {i.count} in stock{i.po_reference ? ` · ${i.po_reference}` : ''}
				</option>
			{/each}
		</select>
	</label>
	{#if tooMuch}
		<p class="muted warn">
			{#if auto}
				Only {available} in stock.
			{:else if source}
				Stock item #{source.id} only holds {source.count}. Tick Automatic to spread it across items.
			{/if}
		</p>
	{/if}

	<div class="actions">
		<button class="btn ghost" type="button" onclick={() => (open = false)}>Cancel</button>
		<button
			class="btn"
			type="button"
			onclick={save}
			disabled={quantity <= 0 || quantity > owedQty || tooMuch || (!auto && sourceId === '')}
		>
			Settle
		</button>
	</div>
</dialog>

<style>
	dialog.settle {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 20px;
		min-width: 320px;
		background: var(--card);
		color: var(--ink);
	}
	dialog.settle::backdrop {
		background: rgba(0, 0, 0, 0.4);
	}
	.check.auto {
		flex-direction: row;
		align-items: center;
		gap: 6px;
		margin-bottom: 6px;
		font-weight: normal;
	}
	select:disabled {
		opacity: 0.5;
	}
	.warn {
		color: var(--warn, #b45309);
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
</style>
