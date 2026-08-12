// A tiny global toast, Svelte 5 runes. Import `toast` and call toast.show(...).
import { ApiError } from './api';

class ToastState {
	message = $state('');
	kind = $state<'ok' | 'err'>('ok');
	visible = $state(false);
	#timer: ReturnType<typeof setTimeout> | undefined;

	show(message: string, kind: 'ok' | 'err' = 'ok') {
		this.message = message;
		this.kind = kind;
		this.visible = true;
		clearTimeout(this.#timer);
		this.#timer = setTimeout(() => (this.visible = false), 3000);
	}

	/** Run an async action, showing its error message (from the API) on failure. */
	async run(action: () => Promise<unknown>, okMessage?: string) {
		try {
			await action();
			if (okMessage) this.show(okMessage, 'ok');
			return true;
		} catch (e) {
			this.show(e instanceof ApiError ? e.message : String(e), 'err');
			return false;
		}
	}
}

export const toast = new ToastState();
