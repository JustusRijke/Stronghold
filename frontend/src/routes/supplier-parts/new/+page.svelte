<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import PickerNew from '$lib/components/PickerNew.svelte';
	import type { Part, Supplier } from '$lib/types';

	let supplierId = $state<number | ''>(Number($page.url.searchParams.get('supplier_id')) || '');
	let sku = $state('');
	let description = $state('');
	let partId = $state<number | ''>(Number($page.url.searchParams.get('part_id')) || '');
	let ean = $state('');
	let hyperlink = $state('');
	let packQty = $state(1);

	let suppliers = $state<Supplier[]>([]);
	let parts = $state<Part[]>([]);
	$effect(() => {
		api.suppliers().then((s) => (suppliers = s.filter((x) => x.active)));
		// only parts a supplier may sell: assemblies are built, and a
		// non-purchasable part is never bought (both rejected by the backend)
		api
			.parts()
			.then((p) => (parts = p.filter((x) => x.active && x.purchasable && !x.assembly)));
	});

	function plabel(p: Part) {
		return p.sku ? `${p.sku} - ${p.description}` : p.description;
	}

	// inline creates: a cold user needs the part and the supplier to exist before
	// this form can be filled at all, so both are creatable without leaving
	let newSupplier = $state(false);
	let newSupplierName = $state('');
	async function createSupplier() {
		try {
			const s = await api.createSupplier({ name: newSupplierName });
			suppliers = [s, ...suppliers];
			supplierId = s.id;
			newSupplier = false;
			newSupplierName = '';
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}

	let newPart = $state(false);
	let newPartSku = $state('');
	let newPartDesc = $state('');
	async function createPart() {
		try {
			const p = await api.createPart({ sku: newPartSku, description: newPartDesc });
			parts = [p, ...parts];
			partId = p.id;
			newPart = false;
			newPartSku = '';
			newPartDesc = '';
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}

	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const sp = await api.createSupplierPart({
				supplier_id: Number(supplierId),
				sku,
				part_id: Number(partId),
				description,
				ean,
				hyperlink,
				pack_qty: packQty
			});
			goto(`/supplier-parts/${sp.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}
</script>

<form class="content nosidebar" onsubmit={create}>
	<h1 class="h1">New supplier part</h1>
	<label class="field req">
		<span>Supplier</span>
		<div class="pickrow">
			<Picker id="supplier" bind:value={supplierId} rows={suppliers} label={(s) => s.name} required />
			<button class="btn ghost" type="button" title="New supplier" onclick={() => (newSupplier = true)}>+</button>
		</div>
	</label>
	<PickerNew title="New supplier" bind:open={newSupplier} onsave={createSupplier}>
		<label class="field req"><span>Name</span><input bind:value={newSupplierName} /></label>
	</PickerNew>
	<label class="field req"><span>Supplier SKU</span><input required bind:value={sku} /></label>
	<label class="field"><span>Description</span><input bind:value={description} /></label>
	<label class="field req">
		<span>Part</span>
		<div class="pickrow">
			<Picker id="part" bind:value={partId} rows={parts} label={plabel} required />
			<button class="btn ghost" type="button" title="New part" onclick={() => (newPart = true)}>+</button>
		</div>
	</label>
	<PickerNew title="New part" bind:open={newPart} onsave={createPart}>
		<label class="field req"><span>SKU</span><input bind:value={newPartSku} /></label>
		<label class="field"><span>Description</span><input bind:value={newPartDesc} /></label>
	</PickerNew>
	<label class="field"><span>EAN</span><input bind:value={ean} /></label>
	<label class="field"><span>URL</span><input bind:value={hyperlink} /></label>
	<label class="field"><span>Pack qty</span><input type="number" min="1" bind:value={packQty} /></label>
	<div><button class="btn" type="submit">Create</button></div>
</form>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
