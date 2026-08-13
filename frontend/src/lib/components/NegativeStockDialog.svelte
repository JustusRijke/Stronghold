<script lang="ts">
	// Record stock a build consumed that was never booked in. Only build orders
	// whose component list holds this part are offered -- anything else could not
	// have used it.
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import type { PartBuild } from '$lib/types';

	interface Props {
		partId: number;
		partLabel: string;
		open: boolean;
		onsaved: () => void;
	}
	let { partId, partLabel, open = $bindable(), onsaved }: Props = $props();

	let dialog = $state<HTMLDialogElement | null>(null);
	let quantity = $state(1);
	let buildId = $state<number | ''>('');
	let builds = $state<PartBuild[]>([]);

	$effect(() => {
		if (open) {
			quantity = 1;
			buildId = '';
			dialog?.showModal();
			api.partConsumedBy(partId).then((b) => {
				// newest first: a shortfall is nearly always on a recent build
				builds = [...b].sort((x, y) => (y.start_date ?? '').localeCompare(x.start_date ?? ''));
				buildId = builds[0]?.id ?? '';
			});
		} else {
			dialog?.close();
		}
	});

	async function save() {
		const ok = await toast.run(
			() =>
				api.addNegativeStock({
					part_id: partId,
					quantity,
					build_id: Number(buildId)
				}),
			'Negative stock item added'
		);
		if (ok) {
			open = false;
			onsaved();
		}
	}
</script>

<dialog bind:this={dialog} class="negstock" onclose={() => (open = false)}>
	<h2 class="h2">Add negative stock item</h2>
	<p class="muted">{partLabel}</p>
	<p class="muted note">
		Only needed for legacy build orders (InvenTree imports) that were completed without their stock
		fully allocated. The shortfall settles automatically when the parts are received on a purchase
		order.
	</p>

	{#if builds.length === 0}
		<!-- only builds that CONSUME the part can owe it. An assembly can be both
		     produced and consumed (a sub-assembly), so this is not about assembly -->
		<p class="muted warn">
			No build order lists this part among its components, so none can owe it. A build order that
			<em>produces</em> this part does not consume it.
		</p>
	{:else}
		<label class="field req">
			<span>Quantity used but never booked</span>
			<input type="number" min="0" step="any" bind:value={quantity} />
		</label>
		<label class="field req">
			<span>Used by build order</span>
			<select bind:value={buildId}>
				{#each builds as b (b.id)}
					<option value={b.id}>
						{b.reference || `BO-${b.id}`}{b.description ? ` - ${b.description}` : ''} &middot; {b.start_date ||
							'no date'}
					</option>
				{/each}
			</select>
		</label>
	{/if}

	<div class="actions">
		<button class="btn ghost" type="button" onclick={() => (open = false)}>Cancel</button>
		<button
			class="btn"
			type="button"
			onclick={save}
			disabled={quantity <= 0 || buildId === '' || builds.length === 0}
		>
			Add
		</button>
	</div>
</dialog>

<style>
	dialog.negstock {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 20px;
		min-width: 320px;
		max-width: 460px;
		background: var(--card);
		color: var(--ink);
	}
	dialog.negstock::backdrop {
		background: rgba(0, 0, 0, 0.4);
	}
	.note {
		font-size: 0.9em;
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
