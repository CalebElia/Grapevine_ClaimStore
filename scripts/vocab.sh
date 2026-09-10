#!/usr/bin/env bash
# Review and rule on vocabulary proposals. See pipeline/review_vocab.py for what a ruling means.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m pipeline.review_vocab "$@"
