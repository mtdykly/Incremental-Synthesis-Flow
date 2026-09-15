#!/usr/bin/env bash
set -euo pipefail
CASE=${1:?Usage: checkout_case.sh <case>}
ROOT=$(git rev-parse --show-toplevel)
SRC="$ROOT/benchmarks/source"
CASE_DIR="$ROOT/benchmarks/cases/$CASE"
if [ ! -e "$SRC/.git" ]; then
    echo 'Missing source checkout; run scripts/benchmark/fetch_source.sh first.' >&2
    exit 1
fi
mkdir -p "$ROOT/results/$CASE/work"
for VERSION in base new; do
    REVISION=$(cat "$CASE_DIR/${VERSION}_commit")
    TARGET="$ROOT/results/$CASE/work/$VERSION"
    if [ -e "$TARGET/.git" ]; then
        if [ "$(git -C "$TARGET" rev-parse HEAD)" != "$(git -C "$SRC" rev-parse "$REVISION^{commit}")" ] ||
           [ -n "$(git -C "$TARGET" status --porcelain)" ]; then
            echo "Existing worktree differs or has local changes: $TARGET" >&2
            exit 1
        fi
    else
        git -C "$SRC" worktree add --detach "$TARGET" "$REVISION"
    fi
done
