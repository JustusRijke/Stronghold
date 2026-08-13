<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import type { Part } from '$lib/types';

	let partId = $state<number | ''>(Number($page.url.searchParams.get('part_id')) || '');
	let quantity = $state(1);
	let reference = $state('');
	let description = $state('');
	let assemblies = $state<Part[]>([]);
	$effect(() => {
		api.parts().then((p) => (assemblies = p.filter((x) => x.assembly && x.active)));
	});

	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const build = await api.createBuild({
				part_id: Number(partId),
				quantity,
				reference,
				description
			});
			goto(`/build-orders/${build.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}
</script>

<form class="content nosidebar" onsubmit={create}>
	<h1 class="h1">New build order</h1>
	<label class="field req">
		<span>Assembly</span>
		<Picker
			id="assembly"
			bind:value={partId}
			rows={assemblies}
			label={(p) => p.sku || p.description}
			required
		/>
	</label>
	<label class="field req">
		<span>Quantity</span>
		<input type="number" min="1" step="1" required bind:value={quantity} />
	</label>
	<label class="field req">
		<span>Description</span><input required bind:value={description} />
	</label>
	<label class="field"><span>Reference</span><input bind:value={reference} /></label>
	<div><button class="btn" type="submit">Create</button></div>
	{#if assemblies.length === 0}
		<p class="muted">No assembly parts yet. Mark a part as an assembly and give it a BOM first.</p>
	{/if}
</form>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
