#!/bin/bash

set -e


CASE=$1


ROOT=$(git rev-parse --show-toplevel)


SRC=$ROOT/benchmarks/source

CASE_DIR=$ROOT/benchmarks/cases/$CASE


BASE=$(cat $CASE_DIR/base_commit)

NEW=$(cat $CASE_DIR/new_commit)


mkdir -p $ROOT/results/$CASE/work


git -C $SRC worktree remove \
    $ROOT/results/$CASE/work/base \
    2>/dev/null || true


git -C $SRC worktree remove \
    $ROOT/results/$CASE/work/new \
    2>/dev/null || true



git -C $SRC worktree add \
    $ROOT/results/$CASE/work/base \
    $BASE


git -C $SRC worktree add \
    $ROOT/results/$CASE/work/new \
    $NEW