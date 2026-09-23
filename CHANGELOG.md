# Changelog

## 0.1.0 — initial public release

Release tag: `v0.1.0` on the `main` branch of `kwc-k/rintel-core`.
The public source distribution omits historical GPL-marked experimental
`runtime_trace` and `perf_topo` modules that are not used by the local server.

- Local-first Rintel Core with SQLite as the default datastore and a loopback UI.
- Locked project-local Python and frontend installation; dependency/capability diagnosis.
- Transactional SQLite schema upgrade and data-preserving uninstall path.
- Source release with deterministic build and SHA-256 checksum.

This entry describes packaging of already-frozen product capabilities; it does not introduce a new analyzer or semantic contract.
