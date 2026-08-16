<script lang="ts">
	import { api } from '$lib/api';
	import type { Activity, ActivityRef } from '$lib/types';

	let rows = $state<Activity[]>([]);
	let query = $state('');
	let action = $state(''); // '' = all actions

	async function load() {
		rows = await api.activity();
	}
	$effect(() => {
		load();
	});

	// distinct action kinds present, for the filter dropdown
	const actions = $derived([...new Set(rows.map((r) => r.action))].sort());

	const filtered = $derived(
		rows.filter(
			(r) =>
				(!action || r.action === action) &&
				(!query || r.message.toLowerCase().includes(query.toLowerCase()))
		)
	);

	// a typed ref -> a route. Single source turning DB refs into URLs.
	const ROUTE: Record<string, string> = {
		part: '/parts',
		stock: '/stock',
		supplier: '/suppliers',
		'supplier-part': '/supplier-parts',
		po: '/purchase-orders',
		build: '/build-orders',
		'sales-order': '/sales-orders'
	};
	const refHref = (r: ActivityRef) => `${ROUTE[r.type] ?? ''}/${r.id}`;

	const fmt = (iso: string) => new Date(iso).toLocaleString();
</script>

<div class="content">
	<div class="toolbar">
		<input class="search" placeholder="Filter message..." bind:value={query} />
		<select bind:value={action}>
			<option value="">All actions</option>
			{#each actions as a (a)}<option value={a}>{a}</option>{/each}
		</select>
		<span class="count">{filtered.length} of {rows.length}</span>
	</div>

	<table>
		<thead>
			<tr>
				<th class="when">When</th>
				<th class="action">Action</th>
				<th>What happened</th>
			</tr>
		</thead>
		<tbody>
			{#each filtered as a (a.id)}
				<tr>
					<td class="when">{fmt(a.at)}</td>
					<td class="action">{a.action}</td>
					<td>
						<span class="msg">{a.message}</span>
						{#each a.refs as r (r.type + r.id)}
							<a class="chip" href={refHref(r)}>{r.label}</a>
						{/each}
					</td>
				</tr>
			{/each}
			{#if filtered.length === 0}
				<tr><td colspan="3" class="empty">Nothing logged yet.</td></tr>
			{/if}
		</tbody>
	</table>
</div>

<style>
	.content {
		padding: 16px 20px;
	}
	.toolbar {
		display: flex;
		gap: 10px;
		align-items: center;
		margin-bottom: 12px;
	}
	.search {
		flex: 0 1 320px;
	}
	.count {
		margin-left: auto;
		color: var(--ink-faint);
		font-size: 0.85rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
	}
	th,
	td {
		text-align: left;
		padding: 8px 12px;
		border-bottom: 1px solid var(--line);
		vertical-align: top;
	}
	th {
		color: var(--ink-soft);
		font-weight: 600;
		font-size: 0.8rem;
	}
	tbody tr:nth-child(even) {
		background: var(--row-alt);
	}
	.when {
		white-space: nowrap;
		color: var(--ink-soft);
		font-variant-numeric: tabular-nums;
	}
	.action {
		white-space: nowrap;
		font-family: var(--mono);
		font-size: 0.82rem;
		color: var(--ink-soft);
	}
	.msg {
		margin-right: 6px;
	}
	.chip {
		display: inline-block;
		margin: 0 4px 2px 0;
		padding: 1px 8px;
		border: 1px solid var(--line);
		border-radius: 999px;
		background: var(--accent-soft);
		color: var(--accent);
		font-size: 0.82rem;
		white-space: nowrap;
	}
	.empty {
		color: var(--ink-faint);
		text-align: center;
	}
</style>
