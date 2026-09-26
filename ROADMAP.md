# Roadmap

Rintel is under active development. This page describes direction, not release commitments; planned work has no promised date or delivery scope.

## Available now

- Local stdio MCP for AI agents over the same product datastore and authority boundaries as the local UI and API. Agent-facing writes remain bounded by existing lifecycle and execution rules.
- Local repository registration and indexing through the Workbench, with SQLite by default.
- Evidence queries that preserve source identity, resolution, coverage, and authority boundaries. Deeper compiler, link, runtime, data, or performance evidence is available only when collected and supported for that repository.
- DesignChange and Git worktree candidate validation, with exact-commit publication checks; a candidate result or Git merge alone is not canonical truth.
- Addressable EDA design nodes and IN/OUT ports. These are TO-BE design objects; observed code relationships still need admissible support.

## Planned, not committed

- Improve the graphical Workbench using feedback from people trying its current workflows.
- Make MCP setup and client compatibility guidance clearer as additional clients are verified.
- Improve how partial and unavailable evidence is presented, while retaining the underlying truth and authority boundaries.
- Broaden compiler-grade support and bounded coverage beyond the currently narrow direct-call path, without treating inferred or dynamic targets as exact.

The [English README](README.md) and [中文 README](README.zh-CN.md) describe the current starting path. Suggestions and usability feedback are welcome through [Support](SUPPORT.md).
