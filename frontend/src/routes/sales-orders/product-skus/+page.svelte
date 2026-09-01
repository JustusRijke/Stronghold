<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import type { Part, ProductSku } from '$lib/types';

	let rows = $state<ProductSku[]>([]);
	let assemblies = $state<Part[]>([]);

	let newSku = $state('');
	let newPart = $state<number | ''>('');

	async function load() {
		const [skus, parts] = await Promise.all([api.productSkus(), api.parts()]);
		rows = skus;
		picked = Object.fromEntries(skus.map((r) => [r.sku, r.part_id]));
		// only an assembly has a BOM to prefill from, so only assemblies are offered
		assemblies = parts.filter((p) => p.assembly);
	}
	$effect(() => {
		load();
	});

	async function add() {
		const sku = newSku.trim();
		if (!sku || newPart === '') return;
		if (await toast.run(() => api.setProductSku({ sku, part_id: Number(newPart) }))) {
			newSku = '';
			newPart = '';
			load();
		}
	}
	// what each row's picker holds; a change only lands when the user confirms it
	let picked = $state<Record<string, number | ''>>({});
	async function repoint(sku: string) {
		const partId = picked[sku];
		if (partId === '' || partId === rows.find((r) => r.sku === sku)?.part_id) return;
		if (await toast.run(() => api.setProductSku({ sku, part_id: partId }))) load();
	}
	async function remove(sku: string) {
		if (await toast.run(() => api.removeProductSku(sku))) load();
	}

	const partLabel = (p: Part) => `${p.sku ? p.sku + ' - ' : ''}${p.description}`;
</script>

<div class="content nosidebar">
	<h1 class="h1">Product SKUs</h1>
	<p class="muted">
		What a sold SKU is made of. WooCommerce knows the SKU it sold, not the parts behind
		it, so each one is mapped to the assembly part whose BOM it consumes. Several SKUs
		may map to the same assembly &mdash; a door-left and a door-right variant are the
		same build. Importing an order, or the &ldquo;Prefill from BOM&rdquo; button on a
		sales order, copies that BOM onto the matching line items. It only ever fills in a
		line that has no parts yet: editing a mapping never rewrites an order.
	</p>

	<table class="skus">
		<thead>
			<tr>
				<th>Sold SKU</th>
				<th>Assembly</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.sku)}
				<tr>
					<td class="mono">{r.sku}</td>
					<td>
						<!-- repointing is the same write as adding: the sku is the key -->
						<Picker
							bind:value={picked[r.sku]}
							rows={assemblies}
							label={partLabel}
							id={`sku-${r.sku}`}
							onenter={() => repoint(r.sku)}
							wide
						/>
					</td>
					<td class="num">
						{#if picked[r.sku] !== r.part_id && picked[r.sku] !== ''}
							<button class="link" onclick={() => repoint(r.sku)}>save</button>
						{/if}
						<button class="link" onclick={() => remove(r.sku)}>remove</button>
					</td>
				</tr>
			{/each}
			{#if rows.length === 0}
				<tr><td colspan="3" class="muted">No product SKUs mapped yet.</td></tr>
			{/if}
			<tr class="add">
				<td>
					<input placeholder="HBT-H-DL" bind:value={newSku} onkeydown={(e) => e.key === 'Enter' && add()} />
				</td>
				<td>
					<Picker
						bind:value={newPart}
						rows={assemblies}
						label={partLabel}
						id="newproductsku"
						onenter={add}
						wide
					/>
				</td>
				<td class="num"><button class="link" onclick={add}>add</button></td>
			</tr>
		</tbody>
	</table>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
	.muted {
		max-width: 70ch;
	}
	table.skus {
		border-collapse: collapse;
		margin-top: 16px;
		min-width: 640px;
	}
	table.skus th,
	table.skus td {
		border-bottom: 1px solid var(--line);
		padding: 6px 10px;
		text-align: left;
	}
	table.skus td.num {
		text-align: right;
	}
	tr.add td {
		border-bottom: none;
	}
</style>
