<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';

	let name = $state('');

	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const s = await api.createSupplier({ name });
			goto(`/suppliers/${s.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}
</script>

<form class="content nosidebar" onsubmit={create}>
	<h1 class="h1">New supplier</h1>
	<label class="field req"><span>Name</span><input required bind:value={name} /></label>
	<div><button class="btn" type="submit">Create</button></div>
</form>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
