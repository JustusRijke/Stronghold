<script lang="ts">
	import TabStrip from '$lib/components/TabStrip.svelte';
	import { supplierPartTabs } from '$lib/tabs.svelte';
	let { children } = $props();
</script>

<TabStrip base="/supplier-parts" tabs={supplierPartTabs} {children} />
