# Rintel

**Evidence-driven software intelligence for humans and AI agents.**

[简体中文](README.zh-CN.md)

> **Active development.** The local CLI, API, and MCP interface are usable today. The graphical Workbench is **experimental / in testing**. Advanced compiler, link, runtime, data, and performance evidence depends on what has actually been collected for a repository.

## Quick start

This is the recommended path for a new local installation:

```bash
git clone https://github.com/kwc-k/rintel-core.git
cd rintel-core
./install.sh
./rintel serve
```

Open <http://127.0.0.1:8000>, then add a local repository to register and index it. The installer needs Git for the clone, `uv`, Node.js 20+, and either `npx` or pnpm 11.22.0. It uses `uv` to obtain Python 3.12 in a project environment and builds the local UI from locked dependencies. PostgreSQL is not required: SQLite is the default. Run `./rintel doctor` (or `./rintel doctor --json`) to see required and optional capabilities.

## What Rintel is for

Rintel turns a repository into a persistent, evidence-backed system model. A human can inspect it in the Workbench; an agent can query the same underlying model through MCP instead of reconstructing the project from repeated file searches.

```text
Repository → evidence model → structure, symbols, topology, flow,
                              design, runtime, build/test, Git state
                           ↘ Workbench · MCP agents · CLI/API
```

Start with a repository or subsystem, trace a path through symbols and relationships, and drill down to the supporting fact, exact source span, or run receipt. Rintel supports indexing and querying Python, TypeScript/JavaScript, Go, Java, C, C++, and Fortran. Evidence depth varies by language, build context, and configured provider; a graph edge is not automatically a compiler-proven call.

Rintel can also keep AS-IS observations separate from TO-BE architecture and flow designs. Design changes, DRC/LVS-style checks, bounded build/test execution, and Git workspaces use their existing authority boundaries; drawing a design edge or merging a branch does not publish canonical truth.

## Evidence is the boundary

Rintel keeps **authority, truth, resolution, coverage, and execution modality** distinct. It does not turn an agent explanation, a design proposal, or a runtime observation into a stronger static claim by implication.

```text
Design intent       ≠ observed source fact
Candidate target    ≠ EXACT resolution
PARTIAL coverage    ≠ COMPLETE coverage
Runtime OBSERVED    ≠ static MUST or PRESENT
Git merge           ≠ Canonical CURRENT
```

A `MISSING` conclusion requires appropriate, scoped coverage. `UNKNOWN` is not `FALSE`; `NOT_OBSERVED` means only that a particular run did not observe something. When evidence is insufficient, the useful result is the unresolved scope, reason, and next evidence needed—not a silent guess. Some current views still expose lower-level formal fields directly while the user-facing presentation evolves.

Supporting material may include source spans, indexer or compiler observations, object/link artifacts, runtime traces, build/test receipts, and Git state. Availability of a mechanism does **not** mean that every repository already has that evidence.

## Use Rintel with an AI agent

The current `main` checkout includes a local stdio MCP server. After installation, add it to Codex from the cloned directory:

```bash
codex mcp add rintel -- "$PWD/rintel" mcp
codex mcp list
```

The default MCP surface is read-oriented. An explicit `./rintel mcp --preset design-execute` exposes additional existing, bounded design/workspace tools; it does not grant arbitrary shell, owner approval, or canonical publication authority. MCP `tools/list` reports the actual surface. The Codex local stdio path has been tested; interoperability with other MCP clients should be verified in those clients.

For example, ask the agent:

> Trace a non-trivial path, show evidence and source for each hop, and explain any `UNKNOWN` or `PARTIAL` boundary without guessing a target.

## Human Workbench

The local UI uses the same evidence model for repository exploration, source and evidence inspection, topology, architecture, flow, design changes, runtime, build/test, and Git state. Browser automation exists, but independent human usability validation is still in progress. Treat the Workbench as **experimental**; its layout and workflow may change.

## Local-first and safe by default

`./rintel serve` binds to `127.0.0.1` by default. Rintel does not expose arbitrary browser-submitted shell execution; build and execution operations require server-owned profiles. External AI clients may process project content under **their own** configuration and data policies.

The default SQLite database is `~/Library/Application Support/Rintel/evidence.db` on macOS, or `$XDG_DATA_HOME/rintel/evidence.db` on Linux (falling back to `~/.local/share/rintel/evidence.db`). Set `RINTEL_DATA_HOME` to choose another local data directory. `RINTEL_DATABASE_URL` enables optional PostgreSQL.

Re-run `./install.sh` to update locked application dependencies. Stop Rintel before `./uninstall.sh`: it removes the project-local environment and UI build/dependencies, **not** your source repositories, Rintel datastore, or user caches. Back up the datastore before an upgrade or manual data removal.

## Current status

| Area | Current `main` checkout |
|---|---|
| Repository indexing, search, source, topology, evidence queries | Available |
| Local MCP for agents | Available; read-oriented by default |
| Architecture, Flow, DesignChange, DRC/LVS | Available within their evidence and authority boundaries |
| Build/test and Git collaboration | Available when configured and authorized |
| Compiler/link and runtime evidence | Conditional on provider, build context, and capture coverage |
| Data/performance conclusions | Only where measured evidence exists |
| Graphical Workbench | Experimental / in testing |
| Reusable task Skills and broader client integrations | In development; not a shipped promise |

The aim is a shared substrate for understanding, design, execution, and verification—not a second truth model for agents. Evidence underneath; action on top; no silent guesses.

## More information

Implementation specifications, acceptance reports, and experiments live under [`docs/`](docs/) and [`analysis_tournament/`](analysis_tournament/), rather than in this README. See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), and [Support](SUPPORT.md) for project policies.

Licensed under [Apache-2.0](LICENSE); see [NOTICE](NOTICE) and [trademark guidance](TRADEMARKS.md).
