<script lang="ts" generics="T extends { id: number }">
	// Type-to-search entity picker: a native <input list> + <datalist>. datalist
	// binds a string, so we reconcile label -> id here and leave `value` at ''
	// when nothing matches, letting `required` block the submit.
	interface Props {
		value: number | '';
		rows: T[];
		label: (row: T) => string;
		id: string;
		required?: boolean;
		placeholder?: string;
	}
	let {
		value = $bindable(),
		rows,
		label,
		id,
		required = false,
		placeholder = 'Type to search…'
	}: Props = $props();

	// labels must be unique for the reverse lookup to work; SKUs are not unique
	// in this schema, so collisions get a #id suffix
	const labels = $derived.by(() => {
		const seen = new Map<string, number>();
		return rows.map((r) => {
			const base = label(r);
			const n = (seen.get(base) ?? 0) + 1;
			seen.set(base, n);
			return { id: r.id, text: n > 1 ? `${base} #${r.id}` : base };
		});
	});

	let text = $state('');
	// keep the box in sync when the id is set from outside (query-param prefill,
	// or an inline create selecting the row it just made)
	$effect(() => {
		const hit = labels.find((l) => l.id === value);
		if (hit && hit.text !== text) text = hit.text;
	});

	function sync() {
		value = labels.find((l) => l.text === text)?.id ?? '';
	}
</script>

<input {id} list={`${id}-list`} {required} {placeholder} bind:value={text} oninput={sync} />
<datalist id={`${id}-list`}>
	{#each labels as l (l.id)}<option value={l.text}></option>{/each}
</datalist>
