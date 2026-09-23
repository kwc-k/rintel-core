#!/bin/sh
set -eu
unset PYTHONPATH VIRTUAL_ENV

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$project_dir"

missing=0
for tool in uv node; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        case "$tool" in
            uv) reason='isolated Python installation'; fix='Install uv: https://docs.astral.sh/uv/getting-started/installation/' ;;
            node) reason='building the local UI'; fix='Install Node.js 20 or newer: https://nodejs.org/' ;;
        esac
        printf 'MISSING %s — needed for %s. %s\n' "$tool" "$reason" "$fix" >&2
        missing=1
    fi
done
if ! command -v git >/dev/null 2>&1; then
    printf 'UNAVAILABLE git — needed for Git repository and worktree features; indexing non-Git folders still works. Install Git: https://git-scm.com/downloads\n' >&2
fi
if command -v pnpm >/dev/null 2>&1 && [ "$(pnpm --version)" = '11.22.0' ]; then
    :
elif ! command -v npx >/dev/null 2>&1; then
    printf 'MISSING pnpm 11.22.0 or npx — needed for locked UI dependencies. Install Node.js with npm/npx or pnpm: https://pnpm.io/installation\n' >&2
    missing=1
fi
if [ "$missing" -ne 0 ]; then
    exit 1
fi

node_major=$(node -p 'Number(process.versions.node.split(".")[0])')
if [ "$node_major" -lt 20 ]; then
    printf 'MISSING Node.js 20+ — needed to build the UI. Install from https://nodejs.org/\n' >&2
    exit 1
fi
run_pnpm() {
    if command -v pnpm >/dev/null 2>&1 && [ "$(pnpm --version)" = '11.22.0' ]; then
        pnpm "$@"
    else
        npx --yes pnpm@11.22.0 "$@"
    fi
}

uv sync --frozen --no-dev --python 3.12
run_pnpm --dir web install --frozen-lockfile
run_pnpm --dir web build

printf '\nInstallation complete. Start Rintel with: ./rintel serve\n'
