<script lang="ts">
	import { page } from '$app/stores';
	import { getDoc, render } from '$lib/docs';

	const slug = $derived($page.params.slug ?? '');
	const doc = $derived(getDoc(slug));
</script>

<div class="content">
	<a class="back" href="/help">&larr; Help</a>
	{#if doc}
		<article>{@html render(doc.body)}</article>
	{:else}
		<p class="muted">No document called "{slug}".</p>
	{/if}
</div>

<style>
	.content {
		padding: 16px 20px;
	}
	.back {
		font-size: 0.85rem;
		color: var(--ink-soft);
	}
	article {
		max-width: 48em;
		line-height: 1.55;
	}
	article :global(h1) {
		font-size: 1.2rem;
		margin: 12px 0 8px;
	}
	article :global(h2) {
		font-size: 1rem;
		margin: 24px 0 6px;
		padding-top: 12px;
		border-top: 1px solid var(--line);
	}
	article :global(h3) {
		font-size: 0.92rem;
		margin: 18px 0 4px;
	}
	article :global(p),
	article :global(li) {
		font-size: 0.88rem;
	}
	article :global(table) {
		border-collapse: collapse;
		margin: 10px 0;
		width: 100%;
		font-size: 0.85rem;
	}
	article :global(th),
	article :global(td) {
		border: 1px solid var(--line);
		padding: 5px 8px;
		text-align: left;
		vertical-align: top;
	}
	article :global(th) {
		background: var(--card);
	}
	article :global(code) {
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: 3px;
		padding: 0 3px;
		font-size: 0.9em;
	}
</style>
