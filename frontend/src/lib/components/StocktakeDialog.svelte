<script lang="ts">
	// Correct a part's counted stock. Always needs a reason. Lowering it takes
	// the difference off one stock item, oldest first. It cannot go below zero:
	// stock a build used but nobody booked is NegativeStockDialog's job.
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import type { StockItem } from '$lib/types';

	interface Props {
		partId: number;
		partLabel: string;
		currentCount: number;
		/** what builds already owe (negative): the lowest a stocktake may count to */
		owed: number;
		open: boolean;
		onsaved: () => void;
	}
	let {
		partId,
		partLabel,
		currentCount,
		owed,
		open = $bindable(),
		onsaved
	}: Props = $props();

	let dialog = $state<HTMLDialogElement | null>(null);
	let count = $state(0);
	let reason = $state('');
	let sourceId = $state<number | ''>('');
	let auto = $state(true);
	let items = $state<StockItem[]>([]);
	let reasons = $state<{ add: string[]; subtract: string[] }>({ add: [], subtract: [] });

	const changed = $derived(count !== currentCount);
	const lowering = $derived(count < currentCount);
	// a stocktake empties the shelf but never deepens a debt
	const belowFloor = $derived(count < owed);
	// finding stock and losing it have little vocabulary in common
	const reasonOptions = $derived(lowering ? reasons.subtract : reasons.add);
	// oldest first, matching the default the backend picks
	const sources = $derived(
		items
			.filter((i) => i.part_id === partId && i.status === 'Available' && i.count > 0)
			.sort((a, b) => a.id - b.id)
	);
	const source = $derived(sources.find((i) => i.id === Number(sourceId)));
	// automatic spills across items FIFO; a chosen item is the only source, so
	// only what sits on it can come off
	const tooMuch = $derived(
		lowering && !auto && source ? currentCount - count > source.count : false
	);

	$effect(() => {
		if (open) {
			count = currentCount;
			reason = '';
			auto = true;
			dialog?.showModal();
			api.stock().then((all) => {
				items = all;
				sourceId = sources[0]?.id ?? '';
			});
			api.stocktakeReasons().then((r) => (reasons = r));
		} else {
			dialog?.close();
		}
	});

	async function save() {
		const ok = await toast.run(
			() =>
				api.stocktake({
					part_id: partId,
					count,
					reason,
					// null lets the backend walk the shelf FIFO
					item_id: lowering && !auto && sourceId !== '' ? Number(sourceId) : null
				}),
			'Stock updated'
		);
		if (ok) {
			open = false;
			onsaved();
		}
	}
</script>

<dialog bind:this={dialog} class="stocktake" onclose={() => (open = false)}>
	<h2 class="h2">Stocktake</h2>
	<p class="muted">{partLabel} &middot; currently <strong>{currentCount}</strong> in stock</p>

	<label class="field req">
		<span>Counted quantity</span>
		<!-- min alone still lets you type "-", so block it unless the part already
		     owes stock (then counting down to that debt is legitimate) -->
		<input
			type="number"
			min={owed}
			step="any"
			bind:value={count}
			onkeydown={(e) => {
				if (e.key === '-' && owed >= 0) e.preventDefault();
			}}
			oninput={(e) => {
				if (owed >= 0 && e.currentTarget.value.includes('-')) e.currentTarget.value = '';
			}}
		/>
	</label>
	{#if owed < 0}
		<p class="muted hint">
			{-owed} already owed to build orders, so this part counts down to {owed}. Use Add negative
			stock item to owe more.
		</p>
	{/if}
	{#if belowFloor}
		<p class="muted warn">Cannot count below {owed}: that would create a new debt.</p>
	{/if}

	{#if changed}
		<label class="field req">
			<span>Reason</span>
			<input bind:value={reason} placeholder="Why does the count differ?" />
		</label>
		<div class="reasons">
			{#each reasonOptions as r (r)}
				<button class="btn ghost small" type="button" onclick={() => (reason = r)}>{r}</button>
			{/each}
		</div>
	{/if}

	{#if lowering}
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
		{#if tooMuch && source}
			<p class="muted warn">
				Stock item #{source.id} only holds {source.count}. Tick Automatic to spread it across items.
			</p>
		{/if}
	{/if}

	<div class="actions">
		<button class="btn ghost" type="button" onclick={() => (open = false)}>Cancel</button>
		<button
			class="btn"
			type="button"
			onclick={save}
			disabled={!changed ||
				belowFloor ||
				!reason ||
				tooMuch ||
				(lowering && !auto && sourceId === '')}
		>
			Save
		</button>
	</div>
</dialog>

<style>
	dialog.stocktake {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 20px;
		min-width: 320px;
		background: var(--card);
		color: var(--ink);
	}
	dialog.stocktake::backdrop {
		background: rgba(0, 0, 0, 0.4);
	}
	.reasons {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		margin: -4px 0 12px;
	}
	.btn.small {
		padding: 2px 8px;
		font-size: 0.85em;
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
	.hint {
		font-size: 0.85em;
		margin-top: -6px;
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
