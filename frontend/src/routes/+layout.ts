// SPA: no server-side rendering, no prerendering of data pages. The adapter's
// `fallback: index.html` serves the shell; FastAPI serves the bundle + API and
// falls back to index.html for deep links. Data is always fetched client-side.
export const ssr = false;
export const prerender = false;
