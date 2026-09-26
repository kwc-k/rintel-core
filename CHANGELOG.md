# Changelog

## 0.2.0 — evidence-bound workloop and agent access

- Package the existing local MCP surface for agents using the same product datastore and authority checks as Rintel's API.
- Close DesignChange/Git worktree candidate validation and the EDA design-write revision guard; keep external edits from reusing stale local Undo history.
- Give current C symbols stable identity and show addressable EDA nodes and ordered IN/OUT ports without treating TO-BE design as canonical evidence.
- Join eligible direct C calls to existing Clang support and bounded coverage in candidate and post-merge production indexing. Unsupported, ambiguous, or unsuitable build context remains `UNKNOWN`; the LVS threshold and truth semantics are unchanged.

The direct-call integration is deliberately bounded. It does not imply whole-program call coverage, stronger dynamic-call truth, or automatic production acceptance without authorized merge and exact-commit re-index.

## 0.1.0 — initial public release

Release tag: `v0.1.0` on the `main` branch of `kwc-k/rintel-core`.
The public source distribution omits historical GPL-marked experimental
`runtime_trace` and `perf_topo` modules that are not used by the local server.

- Local-first Rintel Core with SQLite as the default datastore and a loopback UI.
- Locked project-local Python and frontend installation; dependency/capability diagnosis.
- Transactional SQLite schema upgrade and data-preserving uninstall path.
- Source release with deterministic build and SHA-256 checksum.

This entry describes packaging of already-frozen product capabilities; it does not introduce a new analyzer or semantic contract.
