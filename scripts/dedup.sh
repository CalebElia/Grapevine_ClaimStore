#!/usr/bin/env bash
# Show proposed duplicate subjects. Merges nothing.
#
# WHY A WRAPPER. `python3 -m pipeline.X` only resolves from the repo root, and the failure is
# a ModuleNotFoundError on stderr that reads as "the command did nothing". This cd's first, so
# the tool works from anywhere.
#
#   scripts/dedup.sh                       # the pairs, strongest first
#   scripts/dedup.sh --out proposals.json  # ...and write them for review
#   scripts/dedup.sh --show-merge-sql 110 75   # what ONE merge would run; runs nothing
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m pipeline.dedup_subjects "$@"
