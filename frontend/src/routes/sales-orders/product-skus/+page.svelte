<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import type { Part, ProductSku, SoldSku } from '$lib/types';

	let rows = $state<ProductSku[]>([]);
	let parts = $state<Part[]>([]);
	let sold = $state<SoldSku[]>([]);

	// which sku's "add part" row is open, and what it holds
	let addingTo = $state<string | null>(null);
	let newPart = $state<number | ''>('');
	let newQty = $state(1);

	// the add-a-mapping row at the bottom
	let newSku = $state('');
	let newSkuPart = $state<number | ''>('');
	let newSkuQty = $state(1);

	// SKUs the imported orders use that nothing maps yet -- what the add box
	// suggests, so a key is picked rather than typed (a typo makes a mapping
	// that silently matches nothing)
	const unmapped = $derived(sold.filter((s) => !s.mapped));
	const describe = (sku: string) => sold.find((s) => s.sku === sku)?.description ?? '';

	async function load() {
		const [skus, allParts, soldSkus] = await Promise.all([
			api.productSkus(),
			api.parts(),
			api.soldSkus()
		]);
		rows = skus;
		parts = allParts;
		sold = soldSkus;
	}
	$effect(() => {
		load();
	});

	async function addPart(sku: string) {
		if (newPart === '') return;
		const ok = await toast.run(() =>
			api.addProductSkuPart({ sku, part_id: Number(newPart), quantity: newQty })
		);
		if (ok) {
			addingTo = null;
			newPart = '';
			newQty = 1;
			load();
		}
	}
	async function addSku() {
		const sku = newSku.trim();
		if (!sku || newSkuPart === '') return;
		const ok = await toast.run(() =>
			api.addProductSkuPart({ sku, part_id: Number(newSkuPart), quantity: newSkuQty })
		);
		if (ok) {
			newSku = '';
			newSkuPart = '';
			newSkuQty = 1;
			load();
		}
	}
	async function editPart(linkId: number, quantity: number) {
		if (await toast.run(() => api.editProductSkuPart(linkId, quantity))) load();
	}
	async function removePart(linkId: number) {
		if (await toast.run(() => api.removeProductSkuPart(linkId))) load();
	}

	const partLabel = (p: Part) => `${p.sku ? p.sku + ' - ' : ''}${p.description}`;
</script>

<div class="content nosidebar">
	<h1 class="h1">Product SKUs</h1>
	<p class="muted">
		What a sold SKU is made of. WooCommerce knows the SKU it sold, not the parts behind
		it, so each one is mapped to a list of parts here &mdash; the same shape as the parts
		on a sales order line, and copied onto it verbatim. Map an assembly and the line
		consumes one of that assembly off the shelf; map loose parts and it consumes those.
		Several SKUs may map to the same parts &mdash; a door-left and a door-right variant
		are the same build. Importing an order, or the &ldquo;Prefill from SKUs&rdquo; button
		on a sales order, does the copying, and only ever fills in a line that has no parts
		yet: editing a mapping never rewrites an order.
	</p>
	{#if unmapped.length}
		<p class="muted">
			<strong>{unmapped.length}</strong> SKU{unmapped.length === 1 ? '' : 's'} sold in the
			imported orders {unmapped.length === 1 ? 'is' : 'are'} not mapped yet; the box at the
			bottom suggests them, most-sold first.
		</p>
	{/if}

	{#each rows as row (row.sku)}
		<div class="sku">
			<div class="skuhead">
				<span class="mono code">{row.sku}</span>
				<span class="muted">{describe(row.sku)}</span>
			</div>
			<table class="parts">
				<thead>
					<tr>
						<th>Part</th>
						<th class="num">Per unit</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each row.parts as p (p.id)}
						<tr>
							<td>
								<a href={`/parts/${p.part_id}`}>
									{#if p.part_sku}<span class="mono">{p.part_sku}</span> &mdash; {/if}{p.part_description}
								</a>
								{#if p.part_assembly}<span class="tag">assembly</span>{/if}
							</td>
							<td class="num mono">
								<input
									type="number"
									min="0"
									step="any"
									value={p.quantity}
									onchange={(e) => editPart(p.id, Number(e.currentTarget.value))}
								/>
							</td>
							<td class="num">
								<button class="link" onclick={() => removePart(p.id)}>remove</button>
							</td>
						</tr>
					{/each}
					{#if addingTo === row.sku}
						<tr>
							<td>
								<Picker
									bind:value={newPart}
									rows={parts}
									label={partLabel}
									id={`addpart-${row.sku}`}
									onenter={() => addPart(row.sku)}
									wide
								/>
							</td>
							<td class="num">
								<input type="number" min="0" step="any" bind:value={newQty} />
							</td>
							<td class="num">
								<button class="link" onclick={() => addPart(row.sku)}>add</button>
								<button class="link" onclick={() => (addingTo = null)}>cancel</button>
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
			{#if addingTo !== row.sku}
				<button class="btn ghost small" onclick={() => (addingTo = row.sku)}>Add a part</button>
			{/if}
		</div>
	{/each}
	{#if rows.length === 0}
		<p class="muted">No product SKUs mapped yet.</p>
	{/if}

	<div class="sku new">
		<div class="skuhead"><strong>Map another SKU</strong></div>
		<table class="parts">
			<tbody>
				<tr>
					<td>
						<!-- the description goes in `label`, never in the option's text: a
						     datalist option's text content is what the browser inserts on
						     pick, so writing it there fills the box with the description
						     instead of the sku that is the key -->
						<input
							list="soldskus"
							placeholder={unmapped.length ? 'Pick or type a sold SKU' : 'HBT-H-DL'}
							bind:value={newSku}
						/>
						<datalist id="soldskus">
							{#each unmapped as s (s.sku)}
								<option value={s.sku} label={`${s.sku} - ${s.description} (${s.lines})`}
								></option>
							{/each}
						</datalist>
					</td>
					<td>
						<Picker
							bind:value={newSkuPart}
							rows={parts}
							label={partLabel}
							id="newproductsku"
							onenter={addSku}
							wide
						/>
					</td>
					<td class="num">
						<input type="number" min="0" step="any" bind:value={newSkuQty} />
					</td>
					<td class="num"><button class="link" onclick={addSku}>add</button></td>
				</tr>
			</tbody>
		</table>
	</div>
</div>

<style>
	.nosidebar {
		padding: 16px 20px;
	}
	.muted {
		max-width: 70ch;
	}
	.sku {
		margin: 18px 0;
		border: 1px solid var(--line);
		border-radius: 6px;
		padding: 10px 12px;
		max-width: 900px;
	}
	.skuhead {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 6px;
	}
	.code {
		font-weight: 600;
	}
	.tag {
		font-size: 11px;
		color: var(--ink-faint);
		border: 1px solid var(--line);
		border-radius: 3px;
		padding: 0 4px;
		margin-left: 6px;
	}
	table.parts {
		width: 100%;
		border-collapse: collapse;
	}
	table.parts th,
	table.parts td {
		border-bottom: 1px solid var(--line);
		padding: 5px 8px;
		text-align: left;
	}
	table.parts th {
		font-size: 11px;
		color: var(--ink-faint);
	}
	table.parts td.num,
	table.parts th.num {
		text-align: right;
	}
	.new table.parts td {
		border-bottom: none;
	}
</style>
