<script lang="ts">
	// Anchor sidebar for a detail page: links to `sections` (each an id present as
	// a <section id> in the page), highlighting the one in view. Clicking pins the
	// target briefly so the jump-scroll doesn't re-pick another on short pages.
	let { sections }: { sections: { id: string; label: string }[] } = $props();

	let activeSection = $state('');
	let pinnedUntil = 0;

	function pin(id: string) {
		activeSection = id;
		pinnedUntil = Date.now() + 600;
	}

	$effect(() => {
		sections.length; // re-run when sections appear/change (e.g. async load)
		const marker = 80; // px from viewport top (~ the two sticky bars)
		const onScroll = () => {
			if (Date.now() < pinnedUntil) return;
			const els = [...document.querySelectorAll('section[id]')];
			if (els.length === 0) return;
			const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 2;
			if (atBottom) {
				activeSection = els[els.length - 1].id;
				return;
			}
			let current = els[0].id;
			let best = Infinity;
			for (const el of els) {
				const d = el.getBoundingClientRect().top - marker;
				if (d <= 1 && Math.abs(d) < best) {
					best = Math.abs(d);
					current = el.id;
				}
			}
			activeSection = current;
		};
		onScroll();
		window.addEventListener('scroll', onScroll, { passive: true });
		return () => window.removeEventListener('scroll', onScroll);
	});
</script>

<nav class="sidebar">
	{#each sections as s (s.id)}
		<a href={`#${s.id}`} class:active={activeSection === s.id} onclick={() => pin(s.id)}>{s.label}</a
		>
	{/each}
</nav>

<style>
	.sidebar {
		position: sticky;
		top: calc(var(--tabbar-h) + var(--subtabs-h) + 12px);
	}
</style>
