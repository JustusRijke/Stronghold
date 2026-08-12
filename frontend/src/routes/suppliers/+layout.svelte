<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { supplierTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip base="/suppliers" tabs={supplierTabs} {children} />
