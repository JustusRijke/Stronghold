<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { stockTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip base="/stock" tabs={stockTabs} {children} />
