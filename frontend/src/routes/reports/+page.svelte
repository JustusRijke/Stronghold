<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';

	let refreshing = $state(false);

	// Part prices are kept current by the writes that change them; this is the
	// backfill/repair path (e.g. after an InvenTree import wrote rows directly).
	async function refreshPrices() {
		refreshing = true;
		try {
			const r = await api.refreshPrices();
			toast.show(`Recalculated part prices (${r.priced} parts priced)`);
		} finally {
			refreshing = false;
		}
	}

	const REPORTS = [
		{
			href: '/reports/stock-value',
			title: 'Stock value',
			blurb: 'What the stock on hand is worth, priced from purchase orders.'
		}
	];
</script>

<div class="content">
	<h1>Reports</h1>
	<ul>
		{#each REPORTS as r (r.href)}
			<li>
				<a href={r.href}>{r.title}</a>
				<span>{r.blurb}</span>
			</li>
		{/each}
	</ul>

	<h2>Maintenance</h2>
	<p class="blurb">
		Part prices update automatically when purchase orders or BOMs change. Use this after an import,
		or if a price ever looks wrong.
	</p>
	<button onclick={refreshPrices} disabled={refreshing}>
		{refreshing ? 'Recalculating...' : 'Recalculate all part prices'}
	</button>
</div>

<style>
	.content {
		padding: 16px 20px;
	}
	h1 {
		font-size: 1.2rem;
		margin: 0 0 12px;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
		max-width: 40em;
	}
	li {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 10px 12px;
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	li span {
		color: var(--ink-soft);
		font-size: 0.85rem;
	}
	h2 {
		font-size: 0.95rem;
		margin: 24px 0 4px;
	}
	.blurb {
		color: var(--ink-soft);
		font-size: 0.85rem;
		max-width: 40em;
		margin: 0 0 8px;
	}
</style>
