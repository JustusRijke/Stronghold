<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import type { Part } from '$lib/types';

	let partId = $state<number | ''>(Number($page.url.searchParams.get('part_id')) || '');
	let parts = $state<Part[]>([]);
	$effect(() => {
		api.parts().then((p) => (parts = p.filter((x) => x.active)));
	});

	function label(p: Part) {
		return p.sku ? `${p.sku} - ${p.description}` : p.description;
	}
	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const item = await api.createStock({ part_id: Number(partId) });
			goto(`/stock/${item.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}
</script>

<form class="content nosidebar" onsubmit={create}>
	<h1 class="h1">New stock item</h1>
	<label class="field req">
		<span>Part</span>
		<Picker id="part" bind:value={partId} rows={parts} {label} required />
	</label>
	<div><button class="btn" type="submit">Create</button></div>
</form>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
