<script lang="ts">
	import '../app.css';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { toast } from '$lib/toast.svelte';
	import { api } from '$lib/api';
	import type { SearchResult } from '$lib/types';

	let { children } = $props();

	const NAV = [
		['Home', '/'],
		['Parts', '/parts'],
		['Stock', '/stock'],
		['Suppliers', '/suppliers'],
		['Supplier parts', '/supplier-parts'],
		['Purchase orders', '/purchase-orders'],
		['Build orders', '/build-orders'],
		['Reports', '/reports']
	];

	const SEARCH_ROUTE: Record<SearchResult['type'], string> = {
		part: '/parts',
		supplier: '/suppliers',
		supplier_part: '/supplier-parts',
		purchase_order: '/purchase-orders',
		build_order: '/build-orders'
	};
	const SEARCH_LABEL: Record<SearchResult['type'], string> = {
		part: 'Parts',
		supplier: 'Suppliers',
		supplier_part: 'Supplier parts',
		purchase_order: 'Purchase orders',
		build_order: 'Build orders'
	};
	const SEARCH_ORDER = Object.keys(SEARCH_LABEL) as SearchResult['type'][];

	let searchQuery = $state('');
	let searchIncludeInactive = $state(false);
	let searchResults = $state<SearchResult[]>([]);
	let searchOpen = $state(false);
	let searchTimer: ReturnType<typeof setTimeout>;

	function runSearch() {
		clearTimeout(searchTimer);
		const q = searchQuery.trim();
		if (!q) {
			searchResults = [];
			return;
		}
		searchTimer = setTimeout(async () => {
			searchResults = await api.search(q, searchIncludeInactive);
		}, 200);
	}

	function searchGroups() {
		return SEARCH_ORDER.map((type) => ({
			type,
			label: SEARCH_LABEL[type],
			items: searchResults.filter((r) => r.type === type)
		})).filter((g) => g.items.length > 0);
	}

	function goToResult(r: SearchResult) {
		searchOpen = false;
		searchQuery = '';
		searchResults = [];
		goto(`${SEARCH_ROUTE[r.type]}/${r.id}`);
	}

	// a top-level section is active when the path starts with its base
	function isActive(path: string): boolean {
		const cur = $page.url.pathname;
		if (path === '/') return cur === '/';
		return cur === path || cur.startsWith(path + '/');
	}

	// publish the real tab-bar height so sticky sub-bars and section scroll
	// offsets align exactly (font/zoom-proof, no hardcoded pixel guess)
	let tabbar = $state<HTMLElement>();
	$effect(() => {
		if (!tabbar) return;
		const set = () =>
			document.documentElement.style.setProperty('--tabbar-h', `${tabbar!.offsetHeight}px`);
		set();
		const ro = new ResizeObserver(set);
		ro.observe(tabbar);
		return () => ro.disconnect();
	});
</script>

<nav class="tabbar" bind:this={tabbar}>
	{#each NAV as [label, path] (path)}
		<a href={path} class:active={isActive(path)}>{label}</a>
	{/each}
	<div class="search">
		<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
			<circle cx="11" cy="11" r="7" />
			<path d="m21 21-4.3-4.3" />
		</svg>
		<input
			type="search"
			placeholder="Search..."
			bind:value={searchQuery}
			oninput={runSearch}
			onfocus={() => (searchOpen = true)}
			onblur={() => setTimeout(() => (searchOpen = false), 150)}
		/>
		{#if searchOpen && searchQuery.trim()}
			<label class="include-inactive">
				<input
					type="checkbox"
					bind:checked={searchIncludeInactive}
					onchange={runSearch}
				/>
				inactive
			</label>
		{/if}
		{#if searchOpen && searchQuery.trim() && searchResults.length > 0}
			<div class="results">
				{#each searchGroups() as group (group.type)}
					<div class="group-label">{group.label}</div>
					{#each group.items as r (r.id)}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<a href={`${SEARCH_ROUTE[r.type]}/${r.id}`} onmousedown={(e) => { e.preventDefault(); goToResult(r); }}>
							{r.label}
						</a>
					{/each}
				{/each}
			</div>
		{/if}
	</div>
	<a
		href="/activity"
		class="activity"
		class:active={isActive('/activity')}
		title="Activity log"
		aria-label="Activity log"
	>
		<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
			<circle cx="12" cy="12" r="9" />
			<path d="M12 7v5l3 2" />
		</svg>
	</a>
	<a
		href="/help"
		class="help"
		class:active={isActive('/help')}
		title="Help"
		aria-label="Help"
	>
		<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
			<circle cx="12" cy="12" r="9" />
			<path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4" />
			<path d="M12 17h.01" />
		</svg>
	</a>
	<a
		href="/settings"
		class="settings"
		class:active={isActive('/settings')}
		title="Settings"
		aria-label="Settings"
	>
		<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
			<circle cx="12" cy="12" r="3" />
			<path
				d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"
			/>
		</svg>
	</a>
</nav>

{@render children()}

{#if toast.visible}
	<div class="toast" class:err={toast.kind === 'err'}>{toast.message}</div>
{/if}

<style>
	.search {
		margin-left: auto; /* push search + activity + gear to the far right of the tab bar */
		display: flex;
		align-items: center;
		gap: 0.4em;
		position: relative;
	}
	.search input[type='search'] {
		width: 10em;
		border: none;
		background: transparent;
		font: inherit;
		color: inherit;
	}
	.search input[type='search']:focus {
		outline: none;
	}
	.include-inactive {
		display: flex;
		align-items: center;
		gap: 0.25em;
		font-size: 0.8em;
		white-space: nowrap;
	}
	.results {
		position: absolute;
		top: 100%;
		right: 0;
		min-width: 16em;
		max-height: 60vh;
		overflow-y: auto;
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: 4px;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
		/* above the sticky sub-tab strip (z-index 25-30) -- it used to cover the
		   dropdown, which hid the last groups (build orders) entirely */
		z-index: 60;
	}
	.results .group-label {
		padding: 0.3em 0.6em;
		font-size: 0.75em;
		font-weight: bold;
		opacity: 0.6;
	}
	.results a {
		display: block;
		padding: 0.3em 0.6em;
	}
	.results a:hover {
		background: rgba(127, 127, 127, 0.15);
	}
	.activity,
	.help,
	.settings {
		display: flex;
		align-items: center;
	}
</style>
