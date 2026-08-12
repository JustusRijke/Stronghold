<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import type { Setting } from '$lib/types';

	let settings = $state<Setting[]>([]);
	$effect(() => {
		api.settings().then((s) => (settings = s));
	});

	async function save(key: string, value: string) {
		await toast.run(() => api.setSetting(key, value), 'Saved. Restart to apply.');
	}
</script>

<div class="shell">
	<div class="content">
		<h1 class="h1">Settings</h1>
		{#each settings as s (s.key)}
			<div class="setting">
				<label class="field" style="flex:1">
					<span>{s.key}</span>
					<input value={s.value} onblur={(e) => save(s.key, e.currentTarget.value)} />
				</label>
			</div>
		{/each}
		<p class="muted">Deployment settings live in settings.toml (read-only here).</p>
	</div>
</div>

<style>
	.setting {
		display: flex;
		gap: 10px;
		max-width: 420px;
	}
</style>
