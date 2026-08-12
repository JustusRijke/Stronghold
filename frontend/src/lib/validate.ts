// Thin wrapper over valibot: validate a value against a generated schema
// (src/lib/validators.ts) and get the first error message per field, or null.
import * as v from 'valibot';

export function validate<S extends v.BaseSchema<unknown, unknown, v.BaseIssue<unknown>>>(
	schema: S,
	value: unknown
): { ok: true; data: v.InferOutput<S> } | { ok: false; errors: Record<string, string> } {
	const r = v.safeParse(schema, value);
	if (r.success) return { ok: true, data: r.output };
	const errors: Record<string, string> = {};
	for (const issue of r.issues) {
		const key = issue.path?.[0]?.key;
		if (typeof key === 'string' && !(key in errors)) errors[key] = issue.message;
	}
	return { ok: false, errors };
}
