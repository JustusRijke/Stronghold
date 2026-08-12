<script lang="ts">
	// Correct a part's counted stock. Lowering it needs a reason; counting below
	// zero means a build used stock that was never booked, so it needs the build.
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { STOCKTAKE_REASONS } from '$lib/status';
	import Picker from './Picker.svelte';
	import type { BuildOrder } from '$lib/types';

	interface Props {
		partId: number;
		partLabel: string;
		currentCount: number;
		open: boolean;
		onsaved: () => void;
	}
	let { partId, partLabel, currentCount, open = $bindable(), onsaved }: Props = $props();

	let dialog = $state<HTMLDialogElement | null>(null);
	let count = $state(0);
	let reason = $state('');
	let buildId = $state<number | ''>('');
	let builds = $state<BuildOrder[]>([]);

	const lowering = $derived(count < currentCount);
	const negative = $derived(count < 0);

	$effect(() => {
		if (open) {
			count = currentCount;
			reason = '';
			buildId = '';
			if (builds.length === 0) api.builds().then((b) => (builds = b));
			dialog?.showModal();
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
					build_id: negative ? Number(buildId) : null
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
		<input type="number" step="any" bind:value={count} />
	</label>

	{#if lowering}
		<label class="field req">
			<span>Reason</span>
			<input list="stocktake-reasons" bind:value={reason} placeholder="Why is stock lower?" />
			<datalist id="stocktake-reasons">
				{#each STOCKTAKE_REASONS as r (r)}
					<option value={r}></option>
				{/each}
			</datalist>
		</label>
	{/if}

	{#if negative}
		<label class="field req">
			<span>Used by build order</span>
			<Picker
				id="stocktake-build"
				bind:value={buildId}
				rows={builds}
				label={(b) => b.reference || `BO-${b.id}`}
				required
			/>
		</label>
		<p class="muted">A count below zero is owed to this build, and settles when the parts arrive.</p>
	{/if}

	<div class="actions">
		<button class="btn ghost" type="button" onclick={() => (open = false)}>Cancel</button>
		<button
			class="btn"
			type="button"
			onclick={save}
			disabled={count === currentCount || (lowering && !reason) || (negative && buildId === '')}
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
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
</style>
