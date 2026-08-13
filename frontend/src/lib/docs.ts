import { marked } from 'marked';

// The /docs markdown files, bundled as raw text at build time so the SPA can
// serve them without a backend route. Title = the file's first `# ` heading.
const FILES = import.meta.glob('../../../docs/*.md', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

export type Doc = { slug: string; title: string; body: string };

export const DOCS: Doc[] = Object.entries(FILES)
	.map(([path, body]) => ({
		slug: path.split('/').pop()!.replace(/\.md$/, ''),
		title: /^#\s+(.+)$/m.exec(body)?.[1] ?? path,
		body
	}))
	.sort((a, b) => a.title.localeCompare(b.title));

export const getDoc = (slug: string) => DOCS.find((d) => d.slug === slug);

// our own docs, bundled at build time -- never user input, so no sanitiser
export const render = (md: string) => marked.parse(md, { async: false });
