<script lang="ts">
	import { api } from '$lib/api';
	let title = $state('Stronghold');
	$effect(() => {
		api.settings().then((s) => {
			const t = s.find((x) => x.key === 'gui.title');
			if (t) title = t.value;
		});
	});
</script>

<div class="shell">
	<div class="content">
		<h1 class="h1">Welcome to {title}</h1>
		<p class="muted">Use the tabs above to manage stock and purchasing.</p>
	</div>
</div>
