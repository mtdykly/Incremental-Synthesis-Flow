#!/usr/bin/env bash
# Explicit URL avoids silently fetching an unrelated repository with the same name.
set -euo pipefail
ROOT=$(git rev-parse --show-toplevel)
URL=${1:?Usage: fetch_source.sh <source-repository-url>}
TARGET="$ROOT/benchmarks/source"
if [ -e "$TARGET/.git" ]; then
    git -C "$TARGET" fetch origin
elif [ -d "$TARGET" ] && [ -n "$(ls -A "$TARGET")" ]; then
    echo "Refusing to overwrite nonempty $TARGET" >&2
    exit 1
else
    git clone "$URL" "$TARGET"
fi
for revision in "$ROOT"/benchmarks/cases/*/{base_commit,new_commit}; do
    git -C "$TARGET" cat-file -e "$(cat "$revision")^{commit}"
done
