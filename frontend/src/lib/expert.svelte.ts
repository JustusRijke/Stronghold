// Expert mode: the "expert.mode" domain setting, loaded once and shared. When
// on, order status transitions are unrestricted and stock counts are editable
// in place -- the backend enforces the same rule, this just shapes the UI.
import { api } from './api';

export const expert = $state({ on: false });

export async function loadExpertMode() {
	const s = await api.settings();
	expert.on = s.find((x) => x.key === 'expert.mode')?.value === 'true';
}
