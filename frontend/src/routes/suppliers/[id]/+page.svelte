<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { supplierTabs } from '$lib/tabs.svelte';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import type { PurchaseOrder, Supplier, SupplierPart } from '$lib/types';
	import { PO_STATUS_OPTIONS } from '$lib/status';

	const id = $derived(Number($page.params.id));
	let supplier = $state<Supplier | null>(null);
	let parts = $state<SupplierPart[]>([]);
	let pos = $state<PurchaseOrder[]>([]);
	let notFound = $state(false);

	const sections = [
		{ id: 'details', label: 'Details' },
		{ id: 'supplier-parts', label: 'Supplier parts' },
		{ id: 'purchase-orders', label: 'Purchase orders' }
	];

	async function load() {
		notFound = false;
		try {
			supplier = await api.supplier(id);
		} catch {
			supplier = null;
			notFound = true;
			return;
		}
		supplierTabs.open(id, supplier.name || `Supplier ${id}`);
		parts = await api.supplierPartsOf(id);
		pos = await api.supplierPos(id);
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	async function saveName(v: string) {
		if (await toast.run(() => api.patchSupplier(id, { name: v }))) load();
	}
	async function toggleActive(v: boolean) {
		await toast.run(() => api.patchSupplier(id, { active: v }));
	}

	const partCols: Column<SupplierPart>[] = [
		{ key: 'sku', header: 'SKU', mono: true, width: '150px' },
		{ key: 'part_description', header: 'Part', truncate: true },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'pack_qty', header: 'Pack', mono: true, width: '80px' },
		{ key: 'active', header: 'Active', bool: true, boolPreset: true, width: '90px' }
	];
	const poCols: Column<PurchaseOrder>[] = [
		{ key: 'reference', header: 'PO', mono: true, width: '150px' },
		{
			key: 'status',
			header: 'Status',
			width: '120px',
			statusFilter: true,
			statusOptions: PO_STATUS_OPTIONS
		},
		{ key: 'end_date', header: 'Target', width: '140px' }
	];
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No supplier {id}.</p></div>
{:else if supplier}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">{supplier.name || `Supplier ${supplier.id}`}</h1>
				{#if !supplier.active}<span class="badge">inactive</span>{/if}
			</div>

			<section id="details">
				<label class="field">
					<span>Name</span>
					<input value={supplier.name} onblur={(e) => saveName(e.currentTarget.value)} />
				</label>
				<label class="check">
					<input
						type="checkbox"
						checked={supplier.active}
						onchange={(e) => toggleActive(e.currentTarget.checked)}
					/>
					Active
				</label>
			</section>

			<section id="supplier-parts">
				<h2 class="h2">Supplier parts ({parts.length})</h2>
				<DataTable
					columns={partCols}
					rows={parts}
					href={(p) => `/supplier-parts/${p.id}`}
					storageKey={`/suppliers/${id}/parts`}
					onAdd={() => goto(`/supplier-parts/new?supplier_id=${id}`)}
				/>
			</section>
			<section id="purchase-orders">
				<h2 class="h2">Purchase orders ({pos.length})</h2>
				<DataTable
					columns={poCols}
					rows={pos}
					href={(po) => `/purchase-orders/${po.id}`}
					storageKey={`/suppliers/${id}/pos`}
					onAdd={() => goto(`/purchase-orders/new?supplier_id=${id}`)}
				/>
			</section>
		</div>
	</div>
{/if}
