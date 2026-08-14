<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import type { Setting } from '$lib/types';

	let settings = $state<Setting[]>([]);
	let deployment = $state<Awaited<ReturnType<typeof api.deploymentSettings>> | null>(null);
	$effect(() => {
		api.settings().then((s) => (settings = s));
		api.deploymentSettings().then((d) => (deployment = d));
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
		<h2 class="h2">Deployment</h2>
		<p class="muted">Read-only. Edit the file and restart the app to apply.</p>
		{#if deployment}
			<dl class="paths">
				<dt>settings.toml</dt>
				<dd>{deployment.path}</dd>
				<dt>data file</dt>
				<dd>{deployment.data_file}</dd>
			</dl>
			<pre class="toml">{deployment.text}</pre>
		{/if}
	</div>
</div>

<style>
	.setting {
		display: flex;
		gap: 10px;
		max-width: 420px;
	}
	.paths {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 4px 12px;
		margin: 0 0 12px;
	}
	.paths dt {
		color: var(--ink-soft);
	}
	.paths dd,
	.toml {
		margin: 0;
		font-family: var(--mono);
	}
	.toml {
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--card);
		overflow-x: auto;
		white-space: pre;
	}
</style>
