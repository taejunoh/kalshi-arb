#!/bin/bash
# Installs sim-reliability git hooks from tracked files in scripts/git-hooks/
# into .git/hooks/. Run after cloning or when hook content changes.
#
# Usage: bash scripts/install_git_hooks.sh

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_SRC="$REPO_ROOT/scripts/git-hooks"
HOOK_DST="$REPO_ROOT/.git/hooks"

if [ ! -d "$HOOK_SRC" ]; then
    echo "Source dir missing: $HOOK_SRC"
    exit 1
fi

mkdir -p "$HOOK_DST"
for hook in "$HOOK_SRC"/*; do
    name=$(basename "$hook")
    dst="$HOOK_DST/$name"
    cp "$hook" "$dst"
    chmod +x "$dst"
    echo "Installed: $name"
done

echo
echo "Hooks installed to $HOOK_DST"
echo "Verify: ls -la $HOOK_DST"
