#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ ! -f "$project_dir/pyproject.toml" ] || [ ! -f "$project_dir/web/package.json" ]; then
    printf 'Refusing cleanup: this is not a Rintel project directory.\n' >&2
    exit 1
fi
for target in "$project_dir/.venv" "$project_dir/web/node_modules" "$project_dir/web/dist"; do
    if [ -e "$target" ] || [ -L "$target" ]; then
        attempt=0
        while [ -e "$target" ] || [ -L "$target" ]; do
            rm -rf -- "$target" 2>/dev/null || :
            attempt=$((attempt + 1))
            if [ "$attempt" -ge 3 ]; then
                break
            fi
            sleep 1
        done
        if [ -e "$target" ] || [ -L "$target" ]; then
            printf 'Could not remove %s. Stop any running Rintel process and retry.\n' "$target" >&2
            exit 1
        fi
        printf 'Removed %s\n' "$target"
    fi
done
printf 'Kept the source checkout, user repositories, Rintel datastore, and user cache.\n'
printf 'See README.md for data and cache locations before removing them manually.\n'
