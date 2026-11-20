// The engine only needs these two fields of an account; declared here so the package is
// self-contained. music-radio's lib/auth.ts Account is structurally compatible.
export type Account = { id: string; style: 'home' | 'discovery' };
