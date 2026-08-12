<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { buildTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip base="/build-orders" tabs={buildTabs} {children} />
