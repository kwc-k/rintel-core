# Rintel v0.2.0 release closure — candidate evidence

Base: public `main` commit `68d0c3e9cefb5a84b2d6a47a6cc64c94095417ca`.

The bounded C11 witness now passes the actual Git integration authority path: a DesignChange plans `source → sink`, an agent worktree implements it, server-owned BUILD/TEST pass, the candidate exact-commit index receives existing Clang-issued support and coverage, and support-aware LVS returns `MATCH`. Owner-authorized merge then advances Git; production CURRENT is re-indexed at that merged commit; the DesignChange evaluates to `EVIDENCE_MATCHED`. The support receipt remains `OBSERVED / MAY / EXACT / UNKNOWN`. Neither Clang nor the design is allowed to assert `MUST` or whole-program `COMPLETE`.

Missing or wrong-TU compilation database gives `UNKNOWN` and `NOT_MERGE_ELIGIBLE`. Candidate indexing does not change production CURRENT. Relative compilation-database directories are resolved against the database location in the existing Clang/provider/support path; the original compile command still governs analysis.

Candidate regression: 63 backend tests with isolated PostgreSQL and SQLite paths, 22 web unit tests, typecheck, production build, 20/20 separate-process port-order runs, and real Google Chrome EDA E2E all pass. Chrome observed external MCP mutation without reload, OUT→IN handles for data and control ports, and a port type popover; screenshots are in `analysis_tournament/v020_release_closure/`. A separate Cua visual capture verified the same canvas after reload but could not establish foreground polling, so it is not used for that claim.

Real FAC bounded smoke indexed 375 files from `ff749a2ea0fd8a01f4fc744de4432e93834720f8` into an isolated SQLite database (3,586 nodes, 8,305 edges). The unchanged rerun parsed 0 files. Searches located `PRateCoefficients`, `DGESV`, and `f_dgesv`. This is product regression evidence, not a claim that all FAC CALLs are compiler-resolved.

Known limitation: multiple distinct C source subjects currently create successive revision-bound overlays. Earlier support cannot be treated as current; the receipt labels it `SUPERSEDED`, and LVS must remain `UNKNOWN` where support is insufficient. This release only claims the tested bounded direct-call path with the available `/usr/bin/clang` frontend; absence of that optional frontend must remain `UNKNOWN` rather than fail the product install. See `issue_register.json`.

Two candidate source archives rebuilt byte-identically. The extracted archive installed in a new macOS arm64 directory, reported `rintel 0.2.0`, served the UI over loopback, and exposed an isolated FAC index over HTTP. Final release verdict remains **pending** until the exact merged release commit passes fresh-main and fresh-archive validation. No tag or GitHub Release should be created before that gate.
