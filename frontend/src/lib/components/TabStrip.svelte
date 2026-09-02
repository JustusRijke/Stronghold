<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import type { Tabs } from '$lib/tabs.svelte';

	// Second-level tab strip: an Overview tab plus one tab per opened detail
	// record. Shared by every entity; `base` is the route root (e.g. "/stock")
	// and `tabs` its persisted store. `fixed` adds section pages that are always
	// there and never close (slug relative to base), sitting after Overview.
	let {
		base,
		tabs,
		fixed = [],
		children
	}: {
		base: string;
		tabs: Tabs;
		fixed?: { slug: string; label: string }[];
		children: Snippet;
	} = $props();

	// active tab: the detail id on /{base}/{id}, else Overview (/{base}, /{base}/new)
	// -- a fixed page has no id param, so it leaves activeId null and is matched
	// on the path instead
	const activeId = $derived($page.params.id ? Number($page.params.id) : null);
	const activeSlug = $derived(
		fixed.find((f) => $page.url.pathname === `${base}/${f.slug}`)?.slug ?? null
	);

	function closeTab(e: MouseEvent, id: number) {
		e.preventDefault();
		e.stopPropagation();
		const next = tabs.close(id);
		if (activeId === id) goto(next === null ? base : `${base}/${next}`);
	}

	let menuAt = $state<{ x: number; y: number; id: number } | null>(null);

	function openMenu(e: MouseEvent, id: number) {
		e.preventDefault();
		menuAt = { x: e.clientX, y: e.clientY, id };
	}

	// keep undefined closes everything; otherwise all but that tab
	function closeAllFromMenu(keep?: number) {
		menuAt = null;
		tabs.closeAll(keep);
		if (activeId !== null && activeId !== keep) goto(keep === undefined ? base : `${base}/${keep}`);
	}
</script>

<nav class="subtabs">
	<a href={base} class:active={activeId === null && activeSlug === null}>Overview</a>
	{#each fixed as f (f.slug)}
		<a href={`${base}/${f.slug}`} class:active={activeSlug === f.slug}>{f.label}</a>
	{/each}
	{#each tabs.list as t (t.id)}
		<a
			href={`${base}/${t.id}`}
			class:active={activeId === t.id}
			class="parttab"
			oncontextmenu={(e) => openMenu(e, t.id)}
		>
			<span class="tablabel">{t.label}</span>
			<button
				class="close"
				title="Close tab"
				aria-label="Close tab"
				onclick={(e) => closeTab(e, t.id)}>&times;</button
			>
		</a>
	{/each}
</nav>

{#if menuAt}
	<button class="menu-overlay" aria-label="Dismiss menu" onclick={() => (menuAt = null)}></button>
	{@const menuId = menuAt.id}
	<div class="ctxmenu" style="left: {menuAt.x}px; top: {menuAt.y}px">
		<button onclick={() => closeAllFromMenu()}>Close All</button>
		<button onclick={() => closeAllFromMenu(menuId)}>Close Others</button>
	</div>
{/if}

{@render children()}

<style>
	.subtabs {
		display: flex;
		gap: 2px;
		padding: 0 12px;
		background: var(--paper-2);
		border-bottom: 1px solid var(--line);
		overflow-x: auto;
		overflow-y: hidden; /* only the horizontal tab overflow scrolls, never vertical */
		/* pinned flush under the main tab bar (its measured height, set on :root) */
		position: sticky;
		top: var(--tabbar-h, 40px);
		z-index: 25; /* above the main tab bar so it can never hide behind it */
	}
	.subtabs a {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 7px 12px;
		color: var(--ink-soft);
		font-size: 12px;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		white-space: nowrap;
	}
	.subtabs a:hover {
		color: var(--accent);
		text-decoration: none;
	}
	.subtabs a.active {
		color: var(--accent);
		font-weight: 600;
		border-bottom-color: var(--accent);
	}
	.tablabel {
		max-width: 200px;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.close {
		border: none;
		background: none;
		color: var(--ink-faint);
		font-size: 15px;
		line-height: 1;
		padding: 0 2px;
		cursor: pointer;
	}
	.close:hover {
		color: var(--bad);
	}
	.menu-overlay {
		position: fixed;
		inset: 0;
		z-index: 29;
		border: none;
		background: none;
		cursor: default;
	}
	.ctxmenu {
		position: fixed;
		z-index: 30;
		background: var(--paper-2);
		border: 1px solid var(--line);
		border-radius: 4px;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
		padding: 4px;
	}
	.ctxmenu button {
		display: block;
		width: 100%;
		border: none;
		background: none;
		color: var(--ink-soft);
		font-size: 12px;
		text-align: left;
		white-space: nowrap;
		padding: 6px 10px;
		cursor: pointer;
		border-radius: 3px;
	}
	.ctxmenu button:hover {
		background: var(--paper-3, rgba(128, 128, 128, 0.15));
		color: var(--accent);
	}
</style>
