<script lang="ts">
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { salesOrderTabs } from '$lib/tabs.svelte';
	import DataTable, { type Column } from '$lib/components/DataTable.svelte';
	import DetailSidebar from '$lib/components/DetailSidebar.svelte';
	import Picker from '$lib/components/Picker.svelte';
	import type { Part, SalesOrder, SalesOrderLine, SalesShortage, StockItem } from '$lib/types';
	import { CREATED_COLUMN, soStatusLabel } from '$lib/status';
	import { STATUS_OPTIONS as STOCK_STATUS_OPTIONS } from '$lib/validators';

	const id = $derived(Number($page.params.id));

	let so = $state<SalesOrder | null>(null);
	let lines = $state<SalesOrderLine[]>([]);
	let consumed = $state<StockItem[]>([]);
	let parts = $state<Part[]>([]);
	let shortages = $state<SalesShortage[]>([]);
	let notFound = $state(false);

	// which line's "add part" row is open, and what it holds
	let addingTo = $state<number | null>(null);
	let newPart = $state<number | ''>('');
	let newQty = $state(1);

	const sections = [
		{ id: 'details', label: 'Details' },
		{ id: 'margin', label: 'Margin' },
		{ id: 'lines', label: 'Line items' },
		{ id: 'consumed', label: 'Stock consumed' }
	];

	async function load() {
		notFound = false;
		try {
			so = await api.salesOrder(id);
		} catch {
			so = null;
			notFound = true;
			return;
		}
		salesOrderTabs.open(id, so.reference);
		const [ls, stock, allParts, shorts] = await Promise.all([
			api.salesOrderLines(id),
			api.salesOrderStock(id),
			api.parts(),
			api.salesOrderShortages(id)
		]);
		lines = ls;
		consumed = stock;
		// virtual parts (labour) are offered too: a sale can consume them, and
		// the backend records the cost without drawing any stock down
		parts = allParts;
		shortages = shorts;
	}
	$effect(() => {
		if (!Number.isNaN(id)) load();
	});

	async function addPart(lineId: number) {
		if (newPart === '') return;
		const ok = await toast.run(() =>
			api.addLinePart(id, lineId, { part_id: Number(newPart), quantity: newQty })
		);
		if (ok) {
			addingTo = null;
			newPart = '';
			newQty = 1;
			load();
		}
	}
	async function editPart(linkId: number, quantity: number) {
		if (await toast.run(() => api.editLinePart(linkId, quantity))) load();
	}
	async function removePart(linkId: number) {
		if (await toast.run(() => api.removeLinePart(linkId))) load();
	}

	// book popup
	let dialog = $state<HTMLDialogElement | null>(null);
	async function doBook() {
		if (await toast.run(() => api.bookSalesOrder(id))) {
			dialog?.close();
			load();
		}
	}

	const money = (v: number | null | undefined) => (v == null ? '--' : Number(v).toFixed(2));
	// margin over revenue; null whenever the cost is unknown or revenue is zero
	const pct = (v: number | null | undefined) => (v == null ? '--' : `${Number(v).toFixed(1)}%`);
	const linked = $derived(lines.some((l) => l.parts.length > 0));

	const consumedCols: Column<StockItem>[] = [
		{ key: 'id', header: '#', mono: true, width: '80px' },
		CREATED_COLUMN,
		{ key: 'sku', header: 'SKU', mono: true, width: '140px' },
		{ key: 'description', header: 'Description', truncate: true },
		{ key: 'count', header: 'Count', mono: true, width: '100px' },
		{
			key: 'unit_price',
			header: 'Unit price',
			mono: true,
			width: '110px',
			format: (v) => (v == null ? '--' : Number(v).toFixed(4))
		},
		{
			key: 'status',
			header: 'Status',
			width: '140px',
			statusFilter: true,
			statusOptions: [...STOCK_STATUS_OPTIONS]
		},
		{ key: 'po_reference', header: 'From PO', mono: true, width: '120px', format: (v) => v || '--' }
	];
</script>

{#if notFound}
	<div class="content nosidebar"><p class="muted">No sales order {id}.</p></div>
{:else if so}
	<div class="shell">
		<DetailSidebar {sections} />
		<div class="content">
			<div class="head">
				<h1 class="h1">{so.reference}</h1>
				<span class="badge">{soStatusLabel(so.status)}</span>
				{#if so.booked}<span class="badge">Booked</span>{/if}
			</div>
			<p class="muted">
				WooCommerce order {so.wc_number} &middot; {so.customer_name || 'no customer name'}
				{#if so.date_created}&middot; {so.date_created}{/if}
			</p>

			<section id="details">
				<h2 class="h2">Details</h2>
				<p class="muted">
					These come from WooCommerce and are read-only here; a re-import refreshes them.
				</p>
				<dl class="facts">
					<dt>Customer</dt>
					<dd>{so.customer_name || '--'}</dd>
					<dt>Country</dt>
					<dd>{so.shipping_country || '--'}</dd>
					<dt>Status</dt>
					<dd>{soStatusLabel(so.status)}</dd>
					<dt>Date</dt>
					<dd>{so.date_created ?? '--'}</dd>
					<dt>Shipping charged</dt>
					<dd class="mono">{money(so.shipping_cost)}</dd>
				</dl>
			</section>

			<section id="margin">
				<h2 class="h2">Margin</h2>
				<p class="muted">
					Estimated is what the linked parts are currently worth; realised is what the
					stock this sale actually consumed cost. Shipping is excluded &mdash; it is a
					pass-through, not margin on the goods.
				</p>
				<table class="margin">
					<thead>
						<tr><th></th><th>Estimated</th><th>Realised</th></tr>
					</thead>
					<tbody>
						<tr>
							<th>Revenue</th>
							<td class="mono">{money(so.revenue)}</td>
							<td class="mono">{money(so.revenue)}</td>
						</tr>
						<tr>
							<th>Cost</th>
							<td class="mono">{money(so.estimated_cost)}</td>
							<td class="mono">{money(so.realised_cost)}</td>
						</tr>
						<tr>
							<th>Margin</th>
							<td class="mono">{money(so.estimated_margin)}</td>
							<td class="mono">{money(so.realised_margin)}</td>
						</tr>
						<tr>
							<th>Margin %</th>
							<td class="mono">{pct(so.estimated_margin_pct)}</td>
							<td class="mono">{pct(so.realised_margin_pct)}</td>
						</tr>
					</tbody>
				</table>
				{#if !so.booked}
					<p class="muted">Realised figures appear once the order is booked.</p>
				{/if}
			</section>

			<section id="lines">
				<div class="head">
					<h2 class="h2">Line items</h2>
					<!-- bookable while unbooked, and again once parts have been added
					     to an order already booked (booking consumes the delta) -->
					{#if !so.booked || so.unbooked_parts > 0}
						<button class="btn" onclick={() => dialog?.showModal()}>
							{so.booked ? 'Book added parts' : 'Book order'}
						</button>
					{/if}
				</div>
				<p class="muted">
					What WooCommerce sold, and the parts each line consumes. WooCommerce does not
					know what a product is made of, so the parts are linked by hand.
				</p>
				{#if lines.length === 0}
					<p class="muted">This order has no line items.</p>
				{/if}
				{#each lines as line (line.id)}
					<div class="line">
						<div class="linehead">
							<div>
								{#if line.sku}<span class="mono sku">{line.sku}</span>{/if}
								<strong>{line.description}</strong>
							</div>
							<div class="mono nums">
								{line.quantity} &times; {money(line.unit_price)} = {money(line.line_total)}
							</div>
						</div>
						<table class="parts">
							<thead>
								<tr>
									<th>Part</th>
									<th class="num">Per unit</th>
									<th class="num">Required</th>
									<th class="num">In stock</th>
									<th class="num">Est. price</th>
									<th></th>
								</tr>
							</thead>
							<tbody>
								{#each line.parts as p (p.id)}
									<tr class:short={p.in_stock < p.required}>
										<td>
											<a href={`/parts/${p.part_id}`}>
												{#if p.sku}<span class="mono">{p.sku}</span> &mdash; {/if}{p.description}
											</a>
										</td>
										<td class="num mono">
											{#if so.booked}
												{p.quantity}
											{:else}
												<input
													type="number"
													min="0"
													step="any"
													value={p.quantity}
													onchange={(e) => editPart(p.id, Number(e.currentTarget.value))}
												/>
											{/if}
										</td>
										<td class="num mono">{p.required}</td>
										<td class="num mono">{p.in_stock}</td>
										<td class="num mono">{money(p.estimated_price)}</td>
										<td class="num">
											{#if !so.booked}
												<button class="link" onclick={() => removePart(p.id)}>remove</button>
											{/if}
										</td>
									</tr>
								{/each}
								{#if line.parts.length === 0 && addingTo !== line.id}
									<tr><td colspan="6" class="muted">No parts linked yet.</td></tr>
								{/if}
								{#if addingTo === line.id}
									<tr>
										<td>
											<Picker
												bind:value={newPart}
												rows={parts}
												label={(p) => `${p.sku ? p.sku + ' - ' : ''}${p.description}`}
												id={`addpart-${line.id}`}
												onenter={() => addPart(line.id)}
												wide
											/>
										</td>
										<td class="num"><input type="number" min="0" step="any" bind:value={newQty} /></td>
										<td colspan="3"></td>
										<td class="num">
											<button class="link" onclick={() => addPart(line.id)}>add</button>
											<button class="link" onclick={() => (addingTo = null)}>cancel</button>
										</td>
									</tr>
								{/if}
							</tbody>
						</table>
						{#if addingTo !== line.id}
							<button class="btn ghost small" onclick={() => (addingTo = line.id)}>
								Link a part
							</button>
						{/if}
					</div>
				{/each}
			</section>

			<section id="consumed">
				<h2 class="h2">Stock consumed</h2>
				{#if consumed.length === 0}
					<p class="muted">Nothing consumed yet &mdash; this order is not booked.</p>
				{:else}
					<p class="muted">
						What this sale took off the shelf, at the price that stock actually cost. A
						negative Available row is a shortfall still owed. Receiving those parts on a
						purchase order settles it automatically; if the stock is already on the
						shelf (a build produced it, say), settle it by hand from the stock item.
					</p>
					<DataTable
						columns={consumedCols}
						rows={consumed}
						href={(s) => `/stock/${s.id}`}
						storageKey={`/sales-orders/${id}/stock`}
					/>
				{/if}
			</section>
		</div>
	</div>

	<dialog bind:this={dialog} class="book">
		<h2 class="h2">{so.booked ? 'Book the added parts?' : 'Book this order?'}</h2>
		<p class="muted">
			{#if so.booked}
				Consumes what has been linked since this order was booked, oldest stock first.
				What already went out is untouched.
			{:else if so.unbooked_parts === 0}
				No parts are linked, so nothing comes out of stock. Booking just records that
				this order has been dealt with -- for a sale that consumes no stock of its own.
			{:else}
				Consumes the linked parts from stock, oldest first. WooCommerce owns the sale
				itself; this only records what it took off the shelf.
			{/if}
		</p>
		{#if shortages.length > 0}
			<div class="warn">
				<p class="short"><strong>Short on {shortages.length} part(s):</strong></p>
				<ul>
					{#each shortages as c (c.part_id)}
						<li>{c.description}: requires {c.required}, have {c.in_stock}</li>
					{/each}
				</ul>
				<p class="muted">
					You can book anyway -- the full quantity is consumed and the missing parts are
					recorded as negative stock, valued at the part's estimated price. Receiving
					them on a purchase order clears the shortfall automatically; stock that is
					already on the shelf is settled by hand from the stock item. Either way this
					sale is repriced at what it actually cost.
				</p>
			</div>
		{/if}
		<div class="actions">
			<button class="btn ghost" onclick={() => dialog?.close()}>Cancel</button>
			<button class="btn" onclick={doBook}>Book</button>
		</div>
	</dialog>
{/if}

<style>
	dialog.book {
		border: 1px solid var(--line);
		border-radius: 8px;
		padding: 20px;
		min-width: 360px;
		background: var(--card);
		color: var(--ink);
	}
	dialog.book::backdrop {
		background: rgba(0, 0, 0, 0.4);
	}
	dialog.book .actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
	.warn {
		margin-top: 12px;
	}
	.warn ul {
		margin: 4px 0 8px 20px;
		font-size: 0.9em;
	}
	.short {
		color: var(--bad);
	}
	.line {
		margin: 12px 0 18px;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: 6px;
	}
	.linehead {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 12px;
		margin-bottom: 6px;
	}
	.sku {
		margin-right: 6px;
		color: var(--muted);
	}
	table.parts,
	table.margin {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.92em;
	}
	table.parts th,
	table.parts td,
	table.margin th,
	table.margin td {
		padding: 4px 6px;
		text-align: left;
		border-bottom: 1px solid var(--line);
	}
	table.margin {
		max-width: 420px;
	}
	table.margin td {
		text-align: right;
	}
	.num {
		text-align: right;
	}
	/* the quantity boxes are narrow and right-aligned */
	table.parts input[type='number'] {
		width: 90px;
		text-align: right;
	}
	tr.short td {
		color: var(--bad);
	}
	.btn.small {
		font-size: 0.85em;
		padding: 2px 8px;
	}
	button.link {
		background: none;
		border: none;
		color: var(--link, inherit);
		cursor: pointer;
		text-decoration: underline;
		padding: 0 4px;
		font-size: inherit;
	}
	dl.facts {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 4px 16px;
		margin: 8px 0;
	}
	dl.facts dt {
		color: var(--muted);
	}
	dl.facts dd {
		margin: 0;
	}
</style>
