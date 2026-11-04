#!/usr/bin/env bash
# Set up the Chromium base/ benchmark repo: a shallow sparse clone at the pinned commit,
# the Nexus-MCP index, and the GitNexus index.
#
#   benchmarks/setup_chromium_base.sh            # clone, then both indexes
#   benchmarks/setup_chromium_base.sh --clone-only
#
# Run it from the project root, in the project venv (`pip install -e ".[dev]"`).
# The clone gets only base/ (about 4,200 files). The Nexus-MCP index takes about 45 minutes
# on a laptop; GitNexus takes a few minutes. GitNexus must be the pinned release: the
# script stops with the install command when it is not.
# Each step is skipped when its output is already there. Delete the output to do it again.
set -euo pipefail

NAME="chromium-base"
URL="https://chromium.googlesource.com/chromium/src"
SHA="d8ebdd25980e38badccc265758aa508e6592000e"  # pin of benchmarks/tasks/chromium-base.yaml
MODEL="bge-small-en"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="${CHROMIUM_BASE_DIR:-$SCRIPT_DIR/repos/$NAME}"
PYTHON="${PYTHON:-python3}"

if [ -d "$DEST/.git" ] && [ "$(git -C "$DEST" rev-parse HEAD 2>/dev/null)" = "$SHA" ]; then
  echo "[setup] $NAME already at $SHA, clone skipped"
else
  if [ -e "$DEST" ]; then
    echo "[setup] $DEST exists but is not a clone at $SHA. Move it away, then run again." >&2
    exit 1
  fi
  echo "[setup] cloning base/ of Chromium @ $SHA into $DEST"
  mkdir -p "$DEST"
  git -C "$DEST" init -q
  git -C "$DEST" remote add origin "$URL"
  git -C "$DEST" sparse-checkout set --no-cone '/base/'
  git -C "$DEST" fetch --depth 1 --filter=blob:none origin "$SHA"
  git -C "$DEST" checkout -q --detach FETCH_HEAD
  echo "[setup] $(find "$DEST/base" -type f | wc -l | tr -d ' ') files in base/"
fi

if [ "${1:-}" = "--clone-only" ]; then
  exit 0
fi

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/src:$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
"$PYTHON" -m benchmarks.preindex_models --repo "$NAME" --models "$MODEL"
"$PYTHON" -m benchmarks.setup_gitnexus --repo "$NAME"

echo "[setup] done. Run the benchmark:"
echo "  PYTHONPATH=src:. $PYTHON -m benchmarks.runner --tasks benchmarks/tasks/$NAME.yaml \\"
echo "    --conditions baseline,nexus,gitnexus --embedding-models $MODEL --reps 3 --warm-up"
