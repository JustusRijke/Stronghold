<!--
  The data grid, done natively. Solves everything the NiceGUI/AG-Grid version
  fought with, in-language:
    - every row is a real <a href> (native middle/right-click "open in new tab")
    - per-column text filters + a tri-state boolean filter that IS the control
    - click a header to sort (asc / desc / off)
    - a column-visibility picker and a reset-view control
    - the whole view (filters, sort, hidden columns) persisted to localStorage
      per `storageKey`, across navigation
    - live filtered row count
  Native implementation: plain reactive predicates, markup and styling are ours.
-->
<script lang="ts" module>
	// exported types live in a module script so pages can `import { Column }`
	export type BoolPreset = boolean | null;
	export type Column<T> = {
		key: keyof T & string;
		header: string;
		width?: string;
		mono?: boolean;
		bool?: boolean; // render as check/dash + tri-state filter
		boolPreset?: BoolPreset; // initial filter for bool columns on first visit
		statusFilter?: boolean; // render as a multi-select checklist of the distinct values present
		statusOptions?: string[]; // full set of possible values shown in the checklist; defaults to values present in `rows`
		statusDefaultHide?: string[]; // values unchecked (hidden) on first visit, e.g. ['Complete', 'Cancelled']
		statusLabel?: (value: string) => string; // display text for a stored status code (filter menu)
		cellHref?: (row: T) => string; // this cell links here instead of to the row
		hideByDefault?: boolean; // start hidden (e.g. the debug-only id column)
		truncate?: boolean; // clip overflow to one line with an ellipsis (+ title tooltip)
		format?: (value: T[keyof T & string], row: T) => string | number; // display-only transform
		cellClass?: (row: T) => string; // extra class on the cell, e.g. for conditional color
		edit?: 'text' | 'number' | 'checkbox' | 'select'; // inline-editable cell (fires onEdit)
		step?: string; // number-input granularity; defaults to 'any' (decimals allowed)
		options?: { value: string | number; label: string }[]; // choices for edit:'select'
	};

	type SortDir = 'asc' | 'desc';
</script>

<script lang="ts" generics="T extends Record<string, any>">
	// At this row count (hundreds) plain reactive predicates beat a table engine.
	// Reach for a headless table lib the day a grid needs virtualization/grouping.

	let {
		columns,
		rows,
		href,
		storageKey,
		defaultSort,
		onRemove,
		onAdd,
		onEdit,
		rowAction,
		visibleRows = $bindable()
	}: {
		columns: Column<T>[];
		rows: T[];
		href: (row: T) => string;
		storageKey: string;
		defaultSort?: { key: keyof T & string; dir: SortDir }; // initial sort on first visit
		onRemove?: (row: T) => void;
		rowAction?: { icon: string; title: string; run: (row: T) => void }; // extra per-row button
		onAdd?: () => void; // shows a left-aligned "+" button in the toolbar
		onEdit?: (row: T, key: keyof T & string, value: string | number | boolean) => void;
		visibleRows?: T[]; // bind: to read the rows surviving the filters (e.g. to total them)
	} = $props();

	const hasActions = $derived(!!onRemove || !!rowAction);

	// --- persisted view state (filters + sort + hidden columns) --------------
	type SavedState = {
		text: Record<string, string>;
		bool: Record<string, BoolPreset>;
		statusHidden: Record<string, string[]>; // per statusFilter column: values unchecked
		sort: { key: string; dir: SortDir } | null;
		hidden: string[]; // column keys the user has hidden
	};

	function defaults(): SavedState {
		const bool: Record<string, BoolPreset> = {};
		const statusHidden: Record<string, string[]> = {};
		const hidden: string[] = [];
		for (const c of columns) {
			if (c.bool) bool[c.key] = c.boolPreset ?? null;
			if (c.statusFilter) statusHidden[c.key] = c.statusDefaultHide ?? [];
			if (c.hideByDefault) hidden.push(c.key);
		}
		return { text: {}, bool, statusHidden, sort: defaultSort ?? null, hidden };
	}

	function loadSaved(): SavedState {
		try {
			const raw = localStorage.getItem('view:' + storageKey);
			if (raw) return { ...defaults(), ...JSON.parse(raw) };
		} catch {
			/* ignore */
		}
		return defaults();
	}

	let saved = $state<SavedState>(loadSaved());

	function persist() {
		localStorage.setItem('view:' + storageKey, JSON.stringify(saved));
	}

	// visible columns, in declared order
	const visibleColumns = $derived(columns.filter((c) => !saved.hidden.includes(c.key)));

	// --- filtering + sorting (plain, readable - no framework ceremony) -------
	const filtered = $derived.by(() => {
		const out = rows.filter((row) => {
			for (const c of columns) {
				if (c.bool) {
					const want = saved.bool[c.key];
					if (want !== null && want !== undefined && row[c.key] !== want) return false;
				} else if (c.statusFilter) {
					const hidden = saved.statusHidden[c.key] ?? [];
					if (hidden.includes(String(row[c.key] ?? ''))) return false;
				} else {
					const q = (saved.text[c.key] ?? '').trim().toLowerCase();
					if (q && !String(row[c.key] ?? '').toLowerCase().includes(q)) return false;
				}
			}
			return true;
		});
		const s = saved.sort;
		if (s) {
			const col = columns.find((c) => c.key === s.key);
			const empty = (v: unknown) => v === '' || v == null;
			out.sort((a, b) => {
				// blanks always sort last, in both directions -- flipping them to
				// the top on a descending sort buries the rows that have a value
				const ea = empty(a[s.key]),
					eb = empty(b[s.key]);
				if (ea || eb) return ea && eb ? 0 : ea ? 1 : -1;
				return cmp(a[s.key], b[s.key], !!col?.bool) * (s.dir === 'asc' ? 1 : -1);
			});
		}
		return out;
	});

	// publish the filtered rows so a page can total them (bind:visibleRows)
	$effect(() => {
		visibleRows = filtered;
	});

	// table-layout:fixed hands width-less columns whatever is left over, which
	// is nothing once the fixed ones fill the container -- they collapse to zero.
	// Floor the table's own width so it scrolls instead, keeping every flexible
	// column (e.g. Description) readable.
	const FLEX_MIN_PX = 220;
	const minTableWidth = $derived(
		visibleColumns.reduce(
			(sum, c) => sum + (c.width ? parseInt(c.width, 10) || FLEX_MIN_PX : FLEX_MIN_PX),
			hasActions ? 40 : 0 // the actions column
		)
	);

	// numeric where both look numeric, else case-insensitive string. Empty/blank
	// always sorts as greatest, so on a descending sort empty cells surface on top
	// (e.g. an unset target date reads as "needs attention").
	function cmp(a: unknown, b: unknown, bool: boolean): number {
		if (bool) return Number(a) - Number(b);
		const ea = a === '' || a == null,
			eb = b === '' || b == null;
		if (ea || eb) return ea && eb ? 0 : ea ? 1 : -1;
		const na = Number(a),
			nb = Number(b);
		if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
		return String(a).localeCompare(String(b), undefined, { numeric: true });
	}

	// --- actions -------------------------------------------------------------
	function cycleBool(key: string) {
		const cur = saved.bool[key] ?? null;
		saved.bool[key] = cur === null ? true : cur === true ? false : null;
		persist();
	}
	// options for the status-filter checklist: the column's declared full set,
	// else whatever distinct values happen to be present in the data
	function statusValues(c: Column<T>): string[] {
		if (c.statusOptions) return c.statusOptions;
		return [...new Set(rows.map((r) => String(r[c.key] ?? '')))].sort();
	}
	function toggleStatus(key: string, value: string) {
		const hidden = saved.statusHidden[key] ?? [];
		saved.statusHidden[key] = hidden.includes(value)
			? hidden.filter((v) => v !== value)
			: [...hidden, value];
		persist();
	}
	// pages differ in what sits above the table, so measure rather than assume
	let scrollBox = $state<HTMLDivElement | null>(null);
	$effect(() => {
		if (!scrollBox) return;
		const measure = () =>
			scrollBox?.style.setProperty(
				'--box-top',
				`${scrollBox.getBoundingClientRect().top + window.scrollY}px`
			);
		measure();
		window.addEventListener('resize', measure);
		return () => window.removeEventListener('resize', measure);
	});

	let statusMenuOpen = $state<string | null>(null);
	// the menu is position:fixed so `.table-scroll`'s overflow cannot clip it;
	// that costs it its anchor, so pin it to the button's rect on open. Anchor
	// the near edge (top below the button, or bottom above it) and let the far
	// edge fall where it may -- max-height keeps it on screen either way.
	let statusMenuPos = $state({ top: 'auto', bottom: 'auto', right: 0, maxHeight: 0 });
	const MENU_ROW_PX = 25; // one .menu-item; enough of an estimate to pick a side
	function openStatusMenu(key: string, e: MouseEvent, rows: number) {
		if (statusMenuOpen === key) return (statusMenuOpen = null);
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const below = window.innerHeight - r.bottom - 8;
		const above = r.top - 8;
		// flip up only when below cannot hold it and above is roomier
		const wanted = rows * MENU_ROW_PX + 36;
		const up = below < wanted && above > below;
		statusMenuPos = {
			top: up ? 'auto' : `${r.bottom + 4}px`,
			bottom: up ? `${window.innerHeight - r.top + 4}px` : 'auto',
			right: window.innerWidth - r.right,
			maxHeight: (up ? above : below) - 4
		};
		statusMenuOpen = key;
	}
	function setText(key: string, v: string) {
		saved.text[key] = v;
		persist();
	}
	function toggleSort(key: string) {
		// off -> asc -> desc -> off
		const s = saved.sort;
		if (!s || s.key !== key) saved.sort = { key, dir: 'asc' };
		else if (s.dir === 'asc') saved.sort = { key, dir: 'desc' };
		else saved.sort = null;
		persist();
	}
	function toggleColumn(key: string) {
		saved.hidden = saved.hidden.includes(key)
			? saved.hidden.filter((k) => k !== key)
			: [...saved.hidden, key];
		persist();
	}
	function resetView() {
		saved = defaults();
		persist();
		menuOpen = false;
	}

	function boolIcon(s: BoolPreset): string {
		return s === true ? '✓' : s === false ? '✗' : '—';
	}
	function sortArrow(key: string): string {
		const s = saved.sort;
		if (!s || s.key !== key) return '';
		return s.dir === 'asc' ? '▲' : '▼';
	}

	// column-picker menu
	let menuOpen = $state(false);
</script>

<div class="toolbar">
	{#if onAdd}
		<button class="tool add" title="Add new" aria-label="Add new" onclick={onAdd}>+</button>
	{/if}
	<div class="spacer"></div>
	<div class="menuwrap">
		<button
			class="tool"
			title="Show / hide columns"
			aria-label="Show or hide columns"
			onclick={() => (menuOpen = !menuOpen)}>&#9776;</button
		>
		{#if menuOpen}
			<!-- click-away backdrop -->
			<button class="backdrop" aria-label="Close menu" onclick={() => (menuOpen = false)}
			></button>
			<div class="menu" role="menu">
				<div class="menu-title">Columns</div>
				{#each columns as c (c.key)}
					<label class="menu-item">
						<input
							type="checkbox"
							checked={!saved.hidden.includes(c.key)}
							onchange={() => toggleColumn(c.key)}
						/>
						{c.header}
					</label>
				{/each}
			</div>
		{/if}
	</div>
	<button class="tool" title="Reset view (filters, sort, columns)" aria-label="Reset view" onclick={resetView}
		>&#8635;</button
	>
</div>

<div class="table-scroll" bind:this={scrollBox}>
	<table style="min-width:{minTableWidth}px">
		<thead>
			<tr>
				{#each visibleColumns as c (c.key)}
					<th
						class="sortable"
						class:center={c.bool}
						style={c.width ? `width:${c.width}` : ''}
						onclick={() => toggleSort(c.key)}
						title="Sort by {c.header}"
					>
						<span class="th-label">{c.header}<span class="arrow">{sortArrow(c.key)}</span></span>
					</th>
				{/each}
				{#if hasActions}<th class="actions"></th>{/if}
			</tr>
			<tr class="filters">
				{#each visibleColumns as c (c.key)}
					<th class:center={c.bool}>
						{#if c.bool}
							<button
								class="boolfilter"
								data-state={saved.bool[c.key] ?? 'any'}
								title="Filter: click to cycle any / yes / no"
								onclick={() => cycleBool(c.key)}>{boolIcon(saved.bool[c.key] ?? null)}</button
							>
						{:else if c.statusFilter}
							<div class="menuwrap">
								<button
									class="tool statusfilter"
									title="Filter which statuses to show"
									onclick={(e) => openStatusMenu(c.key, e, statusValues(c).length)}
									>&#9660;</button
								>
								{#if statusMenuOpen === c.key}
									<button
										class="backdrop"
										aria-label="Close menu"
										onclick={() => (statusMenuOpen = null)}
									></button>
									<div
										class="menu fixed"
										role="menu"
										style="top:{statusMenuPos.top}; bottom:{statusMenuPos.bottom}; right:{statusMenuPos.right}px; max-height:{statusMenuPos.maxHeight}px"
									>
										<div class="menu-title">Show</div>
										{#each statusValues(c) as v (v)}
											<label class="menu-item">
												<input
													type="checkbox"
													checked={!(saved.statusHidden[c.key] ?? []).includes(v)}
													onchange={() => toggleStatus(c.key, v)}
												/>
												{c.statusLabel ? c.statusLabel(v) : v}
											</label>
										{/each}
									</div>
								{/if}
							</div>
						{:else}
							<input
								type="text"
								placeholder="filter"
								value={saved.text[c.key] ?? ''}
								oninput={(e) => setText(c.key, e.currentTarget.value)}
							/>
						{/if}
					</th>
				{/each}
				{#if hasActions}<th class="actions"></th>{/if}
			</tr>
		</thead>
		<tbody>
			{#each filtered as row (href(row))}
				<tr>
					{#each visibleColumns as c (c.key)}
						<td
							class:mono={c.mono}
							class:boolcell={c.bool}
							class:trunc={c.truncate}
							class:editcell={c.edit}
							class={c.cellClass?.(row)}
						>
							{#if c.edit === 'checkbox'}
								<input
									type="checkbox"
									checked={!!row[c.key]}
									onchange={(e) => onEdit?.(row, c.key, e.currentTarget.checked)}
								/>
							{:else if c.edit === 'select'}
								<select
									value={row[c.key] ?? ''}
									onchange={(e) => onEdit?.(row, c.key, e.currentTarget.value)}
								>
									{#each c.options ?? [] as o (o.value)}<option value={o.value}>{o.label}</option>{/each}
								</select>
							{:else if c.edit}
								<input
									type={c.edit === 'number' ? 'number' : 'text'}
									step={c.edit === 'number' ? (c.step ?? 'any') : undefined}
									value={row[c.key] ?? ''}
									onchange={(e) =>
										onEdit?.(row, c.key, c.edit === 'number' ? Number(e.currentTarget.value) : e.currentTarget.value)}
								/>
							{:else}
								<a
									href={c.cellHref?.(row) || href(row)}
									title={c.truncate ? String(row[c.key] ?? '') : undefined}
								>
									{#if c.bool}
										<span class="boolmark" data-v={row[c.key] ? 'true' : 'false'}
											>{row[c.key] ? '✓' : '—'}</span
										>
									{:else}
										<span class="celltext">{c.format ? c.format(row[c.key], row) : (row[c.key] ?? '')}</span>
									{/if}
								</a>
							{/if}
						</td>
					{/each}
					{#if hasActions}
						<td class="actions">
							{#if rowAction}
								<button
									class="iconbtn plain"
									title={rowAction.title}
									onclick={(e) => {
										e.preventDefault();
										rowAction?.run(row);
									}}>{rowAction.icon}</button
								>
							{/if}
							{#if onRemove}
								<button
									class="iconbtn"
									title="Remove"
									onclick={(e) => {
										e.preventDefault();
										onRemove?.(row);
									}}>&#128465;</button
								>
							{/if}
						</td>
					{/if}
				</tr>
			{/each}
			{#if filtered.length === 0}
				<tr class="empty">
					<td colspan={visibleColumns.length + (hasActions ? 1 : 0)}>No rows.</td>
				</tr>
			{/if}
		</tbody>
	</table>
</div>
<!-- both numbers: the header used to show the unfiltered total next to a filtered
     table, and nobody could tell which was which -->
<div class="rowcount">
	{filtered.length} of {rows.length} row{rows.length === 1 ? '' : 's'}
</div>

<style>
	.toolbar {
		display: flex;
		align-items: center;
		gap: 4px;
		margin-bottom: 4px;
	}
	.spacer {
		flex: 1;
	}
	.add {
		font-size: 20px;
		font-weight: 600;
	}
	.tool {
		width: 28px;
		height: 26px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--card);
		color: var(--ink-soft);
		font-size: 15px;
		line-height: 1;
	}
	.tool:hover {
		color: var(--accent);
		border-color: var(--accent);
	}
	.menuwrap {
		position: relative;
	}
	.backdrop {
		position: fixed;
		inset: 0;
		background: none;
		border: none;
		z-index: 10;
		cursor: default;
	}
	.menu {
		position: absolute;
		right: 0;
		top: 30px;
		z-index: 11;
		min-width: 170px;
		background: var(--card);
		border: 1px solid var(--line);
		border-radius: 8px;
		box-shadow: var(--shadow);
		padding: 6px;
	}
	/* escapes `.table-scroll`'s overflow clip; positioned inline from the button */
	.menu.fixed {
		position: fixed;
		overflow-y: auto; /* max-height is set inline; scroll rather than run off-screen */
	}
	.menu-title {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--ink-soft);
		padding: 4px 8px;
	}
	.menu-item {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 8px;
		padding: 4px 8px;
		font-size: 13px;
		border-radius: 5px;
		cursor: pointer;
	}
	.menu-item:hover {
		background: var(--paper-2);
	}

	.table-scroll {
		overflow-x: auto;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow);
		/* the h-scrollbar sits at the bottom of the scroll box, which on a long
		   table is far below the fold. Cap the box at the space left below it in
		   the viewport so the scrollbar stays on screen; the table then scrolls
		   inside the box, both ways. --box-top is measured (pages differ: some
		   have summary tiles above the table, some don't). The floor keeps a
		   table that sits low on the page (detail sub-tabs) from being squeezed
		   to a few rows -- it just scrolls the page instead. */
		max-height: max(60vh, calc(100vh - var(--box-top, 160px) - 48px));
		overflow-y: auto;
	}
	thead {
		position: sticky;
		top: 0;
		z-index: 2; /* over the scrolling cells, under the filter menu (11) */
	}
	table {
		border-collapse: collapse;
		width: 100%;
		font-size: 13px;
		/* fixed layout so column widths hold and truncated cells clip instead of
		   wrapping into neighbouring rows on narrow screens */
		table-layout: fixed;
	}
	th,
	td {
		text-align: left;
		border-bottom: 1px solid var(--line);
		padding: 0;
	}
	thead th {
		padding: 6px 12px;
		font-size: 11px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--ink-soft);
		font-weight: 600;
		background: var(--paper-2);
		white-space: nowrap;
	}
	thead th.sortable {
		cursor: pointer;
		user-select: none;
	}
	thead th.sortable:hover {
		color: var(--accent);
	}
	.th-label {
		display: inline-flex;
		align-items: center;
		gap: 4px;
	}
	th.center .th-label {
		justify-content: center;
		width: 100%;
	}
	.arrow {
		font-size: 9px;
		color: var(--accent);
	}
	tr.filters th {
		padding: 4px 8px;
		background: var(--paper-2);
	}
	/* center the bool filter button so it lines up with the centered bool cell */
	tr.filters th.center {
		text-align: center;
	}
	tr.filters input {
		width: 100%;
		padding: 3px 6px;
		font-size: 12px;
	}
	tbody tr:nth-child(odd) {
		background: var(--row-alt);
	}
	tbody tr:hover {
		background: var(--accent-soft);
	}
	/* the whole cell is a link -> right-click anywhere = native "open in new tab" */
	td a {
		display: flex;
		align-items: center;
		height: 30px;
		padding: 0 12px;
		color: inherit;
		text-decoration: none;
		min-width: 0; /* allow the flex child (.celltext) to shrink for truncation */
	}
	.celltext {
		white-space: nowrap;
		overflow: hidden;
	}
	/* clip long text to one line + ellipsis instead of wrapping into other rows */
	td.trunc .celltext {
		text-overflow: ellipsis;
	}
	td.mono a {
		font-family: var(--mono);
	}
	/* generic conditional-color hook for cellClass (e.g. shortage) */
	td.short {
		color: var(--bad);
	}
	td.boolcell a {
		justify-content: center;
		padding: 0;
	}
	.boolmark[data-v='true'] {
		color: var(--good);
		font-weight: 700;
	}
	.boolmark[data-v='false'] {
		color: var(--ink-faint);
	}
	td.editcell {
		padding: 2px 6px;
	}
	td.editcell input[type='text'],
	td.editcell input[type='number'],
	td.editcell select {
		width: 100%;
		box-sizing: border-box;
		padding: 3px 5px;
		border: 1px solid var(--line);
		border-radius: 4px;
		background: var(--card);
		color: var(--ink);
		font: inherit;
	}
	td.editcell input:focus,
	td.editcell select:focus {
		outline: none;
		border-color: var(--accent);
	}
	td.editcell input:invalid {
		border-color: var(--bad);
	}
	td.actions,
	th.actions {
		width: 40px;
		text-align: center;
	}
	.iconbtn {
		border: none;
		background: none;
		color: var(--bad);
		font-size: 15px;
		padding: 4px;
	}
	.iconbtn.plain {
		color: var(--ink-soft);
	}
	.iconbtn.plain:hover {
		color: var(--accent);
	}
	tr.empty td {
		padding: 16px;
		text-align: center;
		color: var(--ink-soft);
	}
	/* tri-state boolean filter box: three visually distinct states = the filter */
	.boolfilter {
		width: 24px;
		height: 22px;
		border: 1px solid var(--line);
		border-radius: 4px;
		background: var(--card);
		font-weight: 700;
		line-height: 1;
	}
	/* drop the lingering mouse-focus ring (keep it for keyboard a11y). A focused
	   button here could wedge Chrome's screenshot compositor after a click. */
	.boolfilter:focus:not(:focus-visible) {
		outline: none;
	}
	.boolfilter:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 1px;
	}
	.boolfilter[data-state='true'] {
		color: var(--good);
		border-color: var(--good);
	}
	.boolfilter[data-state='false'] {
		color: var(--bad);
		border-color: var(--bad);
	}
	.boolfilter[data-state='any'] {
		color: var(--ink-faint);
	}
	.rowcount {
		font-size: 12px;
		color: var(--ink-soft);
		padding: 2px;
	}
</style>
