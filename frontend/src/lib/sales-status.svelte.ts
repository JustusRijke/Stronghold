// The store's own order statuses, slug -> label, loaded once and shared. Not a
// fixed list like the PO/build ones: a WooCommerce store's plugins register
// their own statuses (an order-proposal plugin, Blocks checkout), so both the
// set and its wording -- in the store's own language -- come from the backend,
// which caches them at every import.
import { api } from './api';

export const soStatuses = $state<{ list: { slug: string; label: string }[] }>({ list: [] });

export async function loadSoStatuses() {
	soStatuses.list = await api.salesOrderStatuses();
}

export const soStatusOptions = () => soStatuses.list.map((s) => s.slug);
// falls back to the bare slug: an order must never show a blank status because
// the label cache predates it
export const soStatusLabel = (v: string) =>
	soStatuses.list.find((s) => s.slug === v)?.label ?? v;
