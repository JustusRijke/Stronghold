<script lang="ts">
	import { api } from '$lib/api';
	import { toast } from '$lib/toast.svelte';
	import { loadExpertMode } from '$lib/expert.svelte';
	import { soStatuses } from '$lib/sales-status.svelte';
	import type { Setting } from '$lib/types';

	const EXPERT_KEY = 'expert.mode';
	const NO_DEMAND_KEY = 'sales.no_demand_statuses';
	const WC_PREFIX = 'woocommerce.';

	let settings = $state<Setting[]>([]);
	let testing = $state(false);
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

	// A credential's value never leaves the backend, so the field starts empty
	// and an empty submit means "leave it alone" -- otherwise tabbing past the
	// field would wipe a stored secret. Clearing one is an explicit button.
	async function saveSecret(key: string, input: HTMLInputElement) {
		if (!input.value) return;
		await toast.run(() => api.setSetting(key, input.value), 'Saved');
		input.value = '';
		settings = await api.settings();
	}

	async function clearSecret(key: string) {
		await toast.run(() => api.setSetting(key, ''), 'Cleared');
		settings = await api.settings();
	}

	async function testWooCommerce() {
		testing = true;
		try {
			await toast.run(() => api.testWooCommerce(), 'WooCommerce connection works');
		} finally {
			testing = false;
		}
	}

	// The no-demand statuses are stored as a comma-separated list of slugs, but
	// typing slugs blind is no way to pick them: offer the store's own statuses
	// as checkboxes instead. Core dead statuses (cancelled/refunded/failed) are
	// always dead and not listed -- unticking them would promise something the
	// backend does not honour.
	const ALWAYS_DEAD = ['cancelled', 'refunded', 'failed'];
	const noDemand = $derived(
		(settings.find((s) => s.key === NO_DEMAND_KEY)?.value ?? '')
			.split(',')
			.map((v) => v.trim())
			.filter(Boolean)
	);
	const choosableStatuses = $derived(
		soStatuses.list.filter((s) => !ALWAYS_DEAD.includes(s.slug))
	);
	async function toggleNoDemand(slug: string, on: boolean) {
		const next = on ? [...noDemand, slug] : noDemand.filter((v) => v !== slug);
		await save(NO_DEMAND_KEY, next.join(','));
		settings = await api.settings();
	}

	// woocommerce.* is shown as its own group; everything else is a plain list
	const general = $derived(settings.filter((s) => !s.key.startsWith(WC_PREFIX)));
	const wooCommerce = $derived(settings.filter((s) => s.key.startsWith(WC_PREFIX)));
</script>

<div class="shell">
	<div class="content">
		<h1 class="h1">Settings</h1>
		{#each general as s (s.key)}
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
				{:else if s.key === NO_DEMAND_KEY}
					<div class="field" style="flex:1">
						<span>{s.key}</span>
						<p class="muted">
							Open orders normally ask for stock. Tick a status whose orders should
							not -- a quote, say. Cancelled, refunded and failed never count.
						</p>
						{#each choosableStatuses as st (st.slug)}
							<span class="check">
								<input
									type="checkbox"
									checked={noDemand.includes(st.slug)}
									onchange={(e) => toggleNoDemand(st.slug, e.currentTarget.checked)}
								/>
								{st.label}
							</span>
						{/each}
						{#if choosableStatuses.length === 0}
							<p class="muted">Import sales orders once to see the store's statuses.</p>
						{/if}
					</div>
				{:else}
					<label class="field" style="flex:1">
						<span>{s.key}</span>
						<input value={s.value} onchange={(e) => save(s.key, e.currentTarget.value)} />
					</label>
				{/if}
			</div>
		{/each}
		<h2 class="h2">WooCommerce</h2>
		<p class="muted">
			Where sales orders are imported from. The key and secret are stored encrypted;
			the key that decrypts them lives in a file outside your data, named by
			<code>secrets.key_file</code> in settings.toml. Lose that file and you simply
			enter these again.
		</p>
		{#each wooCommerce as s (s.key)}
			<div class="setting">
				<label class="field" style="flex:1">
					<span>{s.key.replace(WC_PREFIX, '')}</span>
					{#if s.secret}
						<input
							type="password"
							autocomplete="off"
							placeholder={s.configured ? 'configured -- type to replace' : 'not set'}
							onchange={(e) => saveSecret(s.key, e.currentTarget)}
						/>
					{:else}
						<input value={s.value} onchange={(e) => save(s.key, e.currentTarget.value)} />
					{/if}
				</label>
				{#if s.secret && s.configured}
					<button class="btn ghost" type="button" onclick={() => clearSecret(s.key)}>
						Clear
					</button>
				{/if}
			</div>
		{/each}
		<button class="btn" type="button" onclick={testWooCommerce} disabled={testing}>
			{testing ? 'Testing...' : 'Test connection'}
		</button>

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
				<dt>data</dt>
				<dd>{deployment.data_path}</dd>
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
