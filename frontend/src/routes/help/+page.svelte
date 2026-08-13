<script lang="ts">
	import { DOCS } from '$lib/docs';
</script>

<div class="content">
	<h1>Help</h1>
	<p class="blurb">How Stronghold works. More subjects will follow.</p>
	<ul>
		{#each DOCS as d (d.slug)}
			<li><a href={`/help/${d.slug}`}>{d.title}</a></li>
		{/each}
	</ul>
</div>

<style>
	.content {
		padding: 16px 20px;
	}
	h1 {
		font-size: 1.2rem;
		margin: 0 0 4px;
	}
	.blurb {
		color: var(--ink-soft);
		font-size: 0.85rem;
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
		padding: 10px 12px;
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
</style>
