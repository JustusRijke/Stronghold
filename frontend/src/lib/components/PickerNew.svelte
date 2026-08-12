<script lang="ts">
	// "+ new" fieldset that expands under a Picker. Not a <dialog>: a two-field
	// create sitting directly under its own picker needs no backdrop or focus
	// trap. Nested inside a <form>, so buttons must be type="button".
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		open: boolean;
		onsave: () => Promise<void>;
		children: Snippet;
	}
	let { title, open = $bindable(), onsave, children }: Props = $props();

	let busy = $state(false);
	async function save() {
		busy = true;
		try {
			await onsave();
		} finally {
			busy = false;
		}
	}
</script>

{#if open}
	<fieldset class="pickernew">
		<legend>{title}</legend>
		{@render children()}
		<div class="actions">
			<button class="btn" type="button" disabled={busy} onclick={save}>Create</button>
			<button class="btn ghost" type="button" onclick={() => (open = false)}>Cancel</button>
		</div>
	</fieldset>
{/if}

<style>
	.pickernew {
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 10px 12px 12px;
		margin: 6px 0 0;
		max-width: 420px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	legend {
		font-size: 12px;
		color: var(--ink-faint);
		padding: 0 4px;
	}
	.actions {
		display: flex;
		gap: 8px;
	}
</style>
