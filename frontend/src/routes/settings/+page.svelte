<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { loadExpertMode } from '$lib/expert.svelte';
	import type { Setting } from '$lib/types';

	const EXPERT_KEY = 'expert.mode';

	let settings = $state<Setting[]>([]);
	let deployment = $state<Awaited<ReturnType<typeof api.deploymentSettings>> | null>(null);
	$effect(() => {
		api.settings().then((s) => (settings = s));
		api.deploymentSettings().then((d) => (deployment = d));
	});

	async function save(key: string, value: string) {
		const expert = key === EXPERT_KEY;
		await toast.run(() => api.setSetting(key, value), expert ? 'Saved' : 'Saved. Restart to apply.');
		if (expert) await loadExpertMode();
	}
</script>

<div class="shell">
	<div class="content">
		<h1 class="h1">Settings</h1>
		{#each settings as s (s.key)}
			<div class="setting">
				{#if s.key === EXPERT_KEY}
					<label class="field" style="flex:1">
						<span>{s.key}</span>
						<span class="check">
							<input
								type="checkbox"
								checked={s.value === 'true'}
								onchange={(e) => save(s.key, String(e.currentTarget.checked))}
							/>
							Lift order status rules and edit stock counts directly
						</span>
					</label>
				{:else}
					<label class="field" style="flex:1">
						<span>{s.key}</span>
						<input value={s.value} onchange={(e) => save(s.key, e.currentTarget.value)} />
					</label>
				{/if}
			</div>
		{/each}
		<h2 class="h2">Deployment</h2>
		<p class="muted">Read-only. Edit the file and restart the app to apply.</p>
		{#if deployment}
			<dl class="paths">
				<dt>version</dt>
				<dd>Stronghold {deployment.app_version}</dd>
				<!-- always equal in practice: the app migrates the data on startup
				     and refuses to run against a newer file, so this is a plain
				     "what shape is my data" readout -->
				<dt>data schema</dt>
				<dd>version {deployment.data_schema_version}</dd>
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
	.check {
		display: flex;
		align-items: center;
		gap: 8px;
		color: var(--ink-soft);
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
