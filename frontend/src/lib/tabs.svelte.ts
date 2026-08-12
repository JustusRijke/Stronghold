// Second-level tab strip for the Parts section: an Overview tab plus one tab
// per opened part detail. The open list is persisted to localStorage so tabs
// survive a reload. Keyed per-section so the pattern can extend to other
// entities later.
type Tab = { id: number; label: string };

function makeTabs(section: string) {
	const key = `tabs:${section}`;
	const load = (): Tab[] => {
		try {
			return JSON.parse(localStorage.getItem(key) ?? '[]');
		} catch {
			return [];
		}
	};

	let tabs = $state<Tab[]>(typeof localStorage === 'undefined' ? [] : load());

	function persist() {
		localStorage.setItem(key, JSON.stringify(tabs));
	}

	return {
		get list() {
			return tabs;
		},
		// add (or relabel) a tab; called when a detail page mounts
		open(id: number, label: string) {
			const found = tabs.find((t) => t.id === id);
			if (found) found.label = label;
			else tabs.push({ id, label });
			persist();
		},
		// remove a tab; returns the id to navigate to next (or null for overview)
		close(id: number): number | null {
			const i = tabs.findIndex((t) => t.id === id);
			if (i === -1) return null;
			tabs.splice(i, 1);
			persist();
			return tabs[i]?.id ?? tabs[i - 1]?.id ?? null;
		},
		closeAll() {
			tabs.splice(0, tabs.length);
			persist();
		}
	};
}

export type Tabs = ReturnType<typeof makeTabs>;

export const partTabs = makeTabs('parts');
export const stockTabs = makeTabs('stock');
export const supplierTabs = makeTabs('suppliers');
export const supplierPartTabs = makeTabs('supplier-parts');
export const poTabs = makeTabs('purchase-orders');
export const buildTabs = makeTabs('build-orders');
