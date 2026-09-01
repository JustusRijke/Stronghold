<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import type { Part, ProductSku, SoldSku } from '$lib/types';

	let rows = $state<ProductSku[]>([]);
	let parts = $state<Part[]>([]);
	let sold = $state<SoldSku[]>([]);

	let newSku = $state('');
	let newPart = $state<number | ''>('');

	// the SKUs the imported orders actually use and that nothing maps yet -- what
	// the add box suggests, so a key is picked rather than typed (a typo here
	// makes a mapping that silently matches nothing)
	const unmapped = $derived(sold.filter((s) => !s.mapped));

	async function load() {
		const [skus, allParts, soldSkus] = await Promise.all([
			api.productSkus(),
			api.parts(),
			api.soldSkus()
		]);
		rows = skus;
		picked = Object.fromEntries(skus.map((r) => [r.sku, r.part_id]));
		// any part maps: an assembly contributes its BOM, anything else one of itself
		parts = allParts;
		sold = soldSkus;
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
		it, so each one is mapped to a part here. Map an <strong>assembly</strong> and its
		whole bill of materials is copied onto the line; map <strong>any other part</strong>
		and the line consumes one of it per unit sold. Several SKUs may map to the same
		part &mdash; a door-left and a door-right variant are the same build. Importing an
		order, or the &ldquo;Prefill from SKUs&rdquo; button on a sales order, does the
		copying. It only ever fills in a line that has no parts yet: editing a mapping
		never rewrites an order.
	</p>
	{#if unmapped.length}
		<p class="muted">
			<strong>{unmapped.length}</strong> SKU{unmapped.length === 1 ? '' : 's'} sold in the
			imported orders {unmapped.length === 1 ? 'is' : 'are'} not mapped yet; the box below
			suggests them, most-sold first.
		</p>
	{/if}

	<table class="skus">
		<thead>
			<tr>
				<th>Sold SKU</th>
				<th>Part</th>
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
							rows={parts}
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
					<!-- a datalist, not a Picker: the key is the SKU string itself, and a
					     SKU the orders have not used yet is still allowed to be typed -->
					<input
						list="soldskus"
						placeholder={unmapped.length ? 'Pick or type a sold SKU' : 'HBT-H-DL'}
						bind:value={newSku}
						onkeydown={(e) => e.key === 'Enter' && add()}
					/>
					<datalist id="soldskus">
						{#each unmapped as s (s.sku)}
							<option value={s.sku}>{s.description} ({s.lines})</option>
						{/each}
					</datalist>
				</td>
				<td>
					<Picker
						bind:value={newPart}
						rows={parts}
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
