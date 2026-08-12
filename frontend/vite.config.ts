import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		// dev: proxy /api to the FastAPI backend so the app is same-origin in dev
		proxy: { '/api': 'http://localhost:8080' }
	}
});
