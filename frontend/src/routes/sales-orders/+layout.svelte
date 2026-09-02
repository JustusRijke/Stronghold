<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { salesOrderTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip
	base="/sales-orders"
	tabs={salesOrderTabs}
	fixed={[{ slug: 'product-skus', label: 'Product SKUs' }]}
	{children}
/>
