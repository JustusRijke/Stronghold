<script lang="ts">
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { poTabs } from '$lib/tabs.svelte';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import { validate } from '$lib/validate';
	import { BookIn } from '$lib/validators';
	import type { POLine, PurchaseOrder, StockItem, SupplierPart } from '$lib/types';
	import { PO_STATUS_OPTIONS as STATUS_OPTIONS } from '$lib/status';
	import { expert } from '$lib/expert.svelte';
	import { STATUS_OPTIONS as STOCK_STATUS_OPTIONS } from '$lib/validators';

	// mirrors backend transition rules (db.edit_po): Complete only when every
	// line is fully received; Complete/Cancelled are dead ends (no reverting);
	// Cancelled additionally requires nothing received yet (receipts already
	// created real stock that cancelling can't undo).
	function statusAllowed(st: string): boolean {
		if (expert.on) return true;
		if (po?.status === 'Complete' || po?.status === 'Cancelled') return st === po.status;
		if (st === 'Complete') return lines.length > 0 && lines.every((l) => l.received >= l.quantity);
		if (st === 'Cancelled') return !lines.some((l) => l.received > 0);
		return true;
	}

	const id = $derived(Number($page.params.id));

	let po = $state<PurchaseOrder | null>(null);
	let stock = $state<StockItem[]>([]);
	let supplierName = $state('');
	let notFound = $state(false);
	let lines = $state<POLine[]>([]);
	// supplier parts of this PO's supplier, for the add-line picker
	let supplierParts = $state<SupplierPart[]>([]);
	// per-line "receive quantity" input, keyed by line id
	let recvQty = $state<Record<number, number>>({});

	// add-line inputs
	let newSpId = $state<number | ''>('');
	let newQty = $state(1);
	let newPrice = $state(0);

	const sections = [
		{ id: 'details', label: 'Details' },
		{ id: 'lines', label: 'Lines' },
		{ id: 'stock', label: 'Stock received' }
	];

	async function load() {
		notFound = false;
		try {
			po = await api.po(id);
		} catch {
			po = null;
			notFound = true;
			return;
		}
		poTabs.open(id, po.reference);
		const [supplier, poLines, sps, allStock] = await Promise.all([
			api.supplier(po.supplier_id),
			api.poLines(id),
			api.supplierParts(),
			api.stock()
		]);
		supplierName = supplier.name;
		lines = poLines;
		recvQty = Object.fromEntries(lines.map((l) => [l.id, Math.max(l.quantity - l.received, 1)]));
		supplierParts = sps.filter((p) => p.supplier_id === po!.supplier_id && p.active);
		stock = allStock.filter((s) => s.po_id === id);
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	async function save(patch: Partial<PurchaseOrder>) {
		if (await toast.run(() => api.patchPo(id, patch))) load();
	}
	// per-line receive-qty validation error, keyed by line id
	let recvErr = $state<Record<number, string>>({});
	async function saveLine(
		line: POLine,
		patch: { quantity?: number; price?: number },
		el: HTMLInputElement
	) {
		if (patch.quantity === line.quantity || patch.price === line.price) return;
		await toast.run(() => api.editPoLine(line.id, patch));
		// reload either way: on success for the new totals, on failure to show what
		// the server still holds. Svelte leaves the box alone when the underlying
		// value did not change (a rejected edit), so write it back by hand.
		lines = await api.poLines(id);
		const fresh = lines.find((l) => l.id === line.id);
		if (fresh) el.value = String(patch.price !== undefined ? fresh.price : fresh.quantity);
	}
	async function addLine() {
		// not inside a <form>, so no native validation to lean on here
		if (newSpId === '') return toast.show('Pick a supplier part first', 'err');
		if (
			await toast.run(() =>
				api.addPoLine(id, { supplier_part_id: Number(newSpId), quantity: newQty, price: newPrice })
			)
		) {
			newSpId = '';
			newQty = 1;
			newPrice = 0;
			lines = await api.poLines(id);
		}
	}
	async function receive(line: POLine, qty: number) {
		const check = validate(BookIn, { quantity: Number.isFinite(qty) ? qty : undefined });
		if (!check.ok) {
			recvErr[line.id] = qty == null || !Number.isFinite(qty) ? 'enter a quantity' : check.errors.quantity;
			return;
		}
		recvErr[line.id] = '';
		// over-receiving is allowed (supplier may ship extra) but confirmed
		if (
			line.received + qty > line.quantity &&
			!confirm(
				`Receiving ${qty} would bring received to ${line.received + qty}, ` +
					`over the ordered ${line.quantity}. Continue?`
			)
		)
			return;
		if (await toast.run(() => api.bookPoLine(line.id, qty))) {
			lines = await api.poLines(id);
			stock = (await api.stock()).filter((s) => s.po_id === id);
		}
	}
	async function removeLine(line: POLine) {
		if (!confirm('Remove this line?')) return;
		if (await toast.run(() => api.removePoLine(line.id))) lines = await api.poLines(id);
	}
	// once Complete/Cancelled, backend rejects line adds/edits/removes and
	// (when cancelled) receiving -- grey out the matching controls to match
	const locked = $derived(po?.status === 'Complete' || po?.status === 'Cancelled');
	const outstanding = $derived(lines.reduce((n, l) => n + Math.max(l.quantity - l.received, 0), 0));
	async function receiveAll() {
		if (!confirm(`Receive all outstanding quantity on ${lines.length} line(s)?`)) return;
		if (await toast.run(() => api.receiveAllPo(id))) load();
	}

	const stockCols: Column<StockItem>[] = [
		{ key: 'id', header: '#', mono: true, width: '80px' },
		{ key: 'sku', header: 'SKU', mono: true, width: '140px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'count', header: 'Count', mono: true, width: '100px' },
		{
			key: 'status',
			header: 'Status',
			width: '200px',
			statusFilter: true,
			statusOptions: [...STOCK_STATUS_OPTIONS]
		}
	];
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No purchase order {id}.</p></div>
{:else if po}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">{po.reference}</h1>
			</div>
			<p>
				Supplier: <a href={`/suppliers/${po.supplier_id}`}>{supplierName}</a>
				{#if po.status}<span class="muted">&middot; {po.status}</span>{/if}
			</p>

			<section id="details">
				<label class="field">
					<span>Description</span>
					<input
						value={po.description}
						onchange={(e) => save({ description: e.currentTarget.value })}
					/>
				</label>
				<label class="field">
					<span>Status</span>
					<select value={po.status} onchange={(e) => save({ status: e.currentTarget.value })}>
						{#if po.status && !STATUS_OPTIONS.includes(po.status)}
							<option value={po.status}>{po.status}</option>
						{/if}
						{#each STATUS_OPTIONS as st (st)}
							<option value={st} disabled={st !== po.status && !statusAllowed(st)}>{st}</option>
						{/each}
					</select>
				</label>
				<label class="field">
					<span>Supplier ref</span>
					<input
						value={po.supplier_reference}
						onchange={(e) => save({ supplier_reference: e.currentTarget.value })}
					/>
				</label>
				<label class="field req">
					<span>Order date</span>
					<input
						type="date"
						required
						value={po.start_date ?? ''}
						onchange={(e) => e.currentTarget.value && save({ start_date: e.currentTarget.value })}
					/>
				</label>
				<label class="field">
					<span>Target date</span>
					<input
						type="date"
						value={po.end_date ?? ''}
						onchange={(e) => save({ end_date: e.currentTarget.value || null })}
					/>
				</label>
				<label class="field">
					<span>Delivery cost</span>
					<input
						type="number"
						min="0"
						step="any"
						value={po.delivery_cost}
						onchange={(e) => save({ delivery_cost: Number(e.currentTarget.value) })}
					/>
				</label>
			</section>

			<section id="lines">
				<div class="head">
					<h2 class="h2">Lines</h2>
					{#if outstanding > 0 && po.status !== 'Cancelled'}
						<button class="btn" onclick={receiveAll}>Receive all ({outstanding})</button>
					{/if}
				</div>
				{#if locked}
					<p class="muted">This order is {po.status}; lines can no longer be changed.</p>
				{/if}
				{#if lines.length === 0}<p class="muted">No lines.</p>{/if}
				{#if lines.length > 0}
					<table class="lines">
						<thead>
							<tr>
								<th>Supplier part</th>
								<th class="num">Qty</th>
								<th class="num">Pack</th>
								<th class="num">Unit price</th>
								<th class="num">Line total</th>
								<th class="num">Received</th>
								{#if !locked}<th></th>{/if}
							</tr>
						</thead>
						<tbody>
							{#each lines as line (line.id)}
								<tr>
									<td>
										<a class="mono" href={`/supplier-parts/${line.supplier_part_id}`}
											>{line.supplier_sku || line.supplier_part_id}</a
										>
									</td>
									<td class="num">
										{#if locked}
											{line.quantity}
										{:else}
											<input
												class="qty"
												type="number"
												min="1"
												step="1"
												value={line.quantity}
												onchange={(e) =>
												saveLine(line, { quantity: Number(e.currentTarget.value) }, e.currentTarget)}
											/>
										{/if}
									</td>
									<td class="num">{line.pack_qty}</td>
									<td class="num">
										{#if locked}
											{line.price.toFixed(4)}
										{:else}
											<input
												class="qty"
												type="number"
												min="0"
												step="any"
												value={line.price}
												onchange={(e) =>
												saveLine(line, { price: Number(e.currentTarget.value) }, e.currentTarget)}
											/>
										{/if}
									</td>
									<td class="num">{(line.quantity * line.price).toFixed(2)}</td>
									<td class="num" class:over={line.received > line.quantity}>
										{line.received}/{line.quantity}
									</td>
									{#if !locked}
										<td class="actions">
											<input
												class="qty"
												class:bad={recvErr[line.id]}
												type="number"
												min="1"
												bind:value={recvQty[line.id]}
											/>
											<button class="btn" onclick={() => receive(line, recvQty[line.id])}>Receive</button>
											<button class="iconbtn" title="Remove" onclick={() => removeLine(line)}
												>&#128465;</button
											>
											{#if recvErr[line.id]}<span class="err">{recvErr[line.id]}</span>{/if}
										</td>
									{/if}
								</tr>
							{/each}
						</tbody>
						<tfoot>
							<tr>
								<td colspan="4">Total</td>
								<td class="num">{lines.reduce((t, l) => t + l.quantity * l.price, 0).toFixed(2)}</td>
								<td colspan={locked ? 1 : 2}></td>
							</tr>
						</tfoot>
					</table>
				{/if}

				{#if !locked}
					<div class="linerow add">
						<Picker
							id="newline-sp"
							bind:value={newSpId}
							rows={supplierParts}
							label={(p) =>
								p.sku && p.description ? `${p.sku} - ${p.description}` : p.sku || p.description}
							placeholder="Supplier part…"
						/>
						<input class="qty" type="number" min="1" bind:value={newQty} />
						<input class="qty" type="number" min="0" step="any" bind:value={newPrice} />
						<button class="btn" onclick={addLine}>Add line</button>
					</div>
				{/if}
			</section>

			<section id="stock">
				<h2 class="h2">Stock received</h2>
				<DataTable
					columns={stockCols}
					rows={stock}
					href={(s) => `/stock/${s.id}`}
					storageKey={`/purchase-orders/${id}/stock`}
				/>
			</section>
		</div>
	</div>
{/if}

<style>
	.linerow {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.linerow.add {
		margin-top: 10px;
	}
	table.lines {
		width: 100%;
		border-collapse: collapse;
	}
	table.lines th,
	table.lines td {
		padding: 4px 8px;
		border-bottom: 1px solid var(--line);
		text-align: left;
		white-space: nowrap;
	}
	table.lines th {
		font-size: 12px;
		color: var(--ink-soft);
		font-weight: 500;
	}
	table.lines .num {
		text-align: right;
	}
	table.lines td.actions {
		display: flex;
		align-items: center;
		gap: 8px;
		justify-content: flex-end;
	}
	table.lines tfoot td {
		border-bottom: none;
		font-weight: 600;
	}
	.qty {
		width: 80px;
	}
	table.lines .qty {
		text-align: right;
	}
	.over {
		color: var(--accent);
	}
	.qty.bad {
		border-color: var(--bad);
	}
	.err {
		color: var(--bad);
		font-size: 12px;
	}
</style>
