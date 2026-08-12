<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';

	let sku = $state('');
	let description = $state('');
	let virtual = $state(false);

	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const p = await api.createPart({ sku, description, virtual });
			goto(`/parts/${p.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}
</script>

<form class="content nosidebar" onsubmit={create}>
	<h1 class="h1">New part</h1>
	<label class="field req"><span>SKU</span><input required bind:value={sku} /></label>
	<label class="field"><span>Description</span><input bind:value={description} /></label>
	<label class="check"><input type="checkbox" bind:checked={virtual} /> Virtual (unlimited stock, no stock items)</label>
	<div><button class="btn" type="submit">Create</button></div>
</form>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
