<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import PickerNew from '$lib/components/PickerNew.svelte';
	import type { Supplier } from '$lib/types';

	let supplierId = $state<number | ''>(Number($page.url.searchParams.get('supplier_id')) || '');
	let reference = $state('');
	let description = $state('');
	// the order date drives "latest price"; default to today
	let startDate = $state(new Date().toISOString().slice(0, 10));
	let suppliers = $state<Supplier[]>([]);

	// restrict the picker to suppliers that carry this part, when arriving from a part's
	// "add PO" flow (see parts/[id]/+page.svelte); absent otherwise -- shows all suppliers
	const allowedIds = $page.url.searchParams.get('supplier_ids');
	const allowed = allowedIds ? new Set(allowedIds.split(',').map(Number)) : null;

	$effect(() => {
		api.suppliers().then((s) => {
			suppliers = s.filter((x) => x.active && (!allowed || allowed.has(x.id)));
		});
	});

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

	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const po = await api.createPo({
				supplier_id: Number(supplierId),
				reference,
				description,
				start_date: startDate
			});
			goto(`/purchase-orders/${po.id}`);
		} catch (e) {
			toast.show(e instanceof Error ? e.message : String(e), 'err');
		}
	}
</script>

<form class="content nosidebar" onsubmit={create}>
	<h1 class="h1">New purchase order</h1>
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
	<label class="field req">
		<span>Description</span>
		<input required bind:value={description} />
	</label>
	<label class="field">
		<span>Reference</span>
		<input bind:value={reference} placeholder="PO-… (auto if left blank)" />
	</label>
	<label class="field req">
		<span>Order date</span>
		<input type="date" required bind:value={startDate} />
	</label>
	<div><button class="btn" type="submit">Create</button></div>
</form>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
</style>
