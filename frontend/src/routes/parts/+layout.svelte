<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { partTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip base="/parts" tabs={partTabs} {children} />
