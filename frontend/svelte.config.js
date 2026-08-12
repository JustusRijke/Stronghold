import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		// SPA: prerender the shell, client-side routing for everything else.
		// FastAPI serves the built output (see backend/main.py) and falls back
		// to index.html for deep links.
		adapter: adapter({ fallback: 'index.html' }),
		prerender: { entries: [] }
	}
};

export default config;
