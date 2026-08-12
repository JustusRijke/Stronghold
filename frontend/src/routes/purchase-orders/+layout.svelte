<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { poTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip base="/purchase-orders" tabs={poTabs} {children} />
