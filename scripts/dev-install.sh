#!/usr/bin/env bash
#
# dev-install.sh — real editable install for Mojo's flat module layout.
#
# ``pip install -e .`` alone is **not** actually editable for this
# repo. Hatch's ``force-include`` (see pyproject.toml) copies the
# flat-layout modules at the repo root (scan.py, init.py, db_ops.py,
# …) into site-packages as static snapshots at install time, and
# those copies win over the source tree in sys.path order. The upshot
# is that edits to any of those files silently stop propagating until
# the next ``pip install -e .`` — which defeats the point of editable.
#
# I spent several rounds trying to fix this in pyproject.toml alone
# (dev-mode-dirs, empty force-include overrides, only-include
# allow-lists, empty packages). Hatch's editable target inherits from
# the wheel target in a way that keeps the force-include copies no
# matter what I set. Restructuring the flat layout into a package
# directory is the "right" fix but invasive — it touches every import
# in the codebase.
#
# This script is the pragmatic workaround: run ``pip install -e .``
# as normal, then overwrite the stale site-packages copies with
# symlinks back to the source tree. Rerun it after every ``pip
# install -e .`` or after pulling new code.
#
# Usage:
#   ./scripts/dev-install.sh             # uses the currently-active python
#   PYTHON=/path/to/python ./scripts/dev-install.sh

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PYTHON="${PYTHON:-python}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: $PYTHON not on PATH" >&2
    exit 1
fi

echo "[dev-install] pip install -e . using $($PYTHON --version)"
"$PYTHON" -m pip install -e . --quiet

SITE_PKG="$($PYTHON -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
if [[ ! -d "$SITE_PKG" ]]; then
    echo "error: could not locate site-packages ($SITE_PKG)" >&2
    exit 1
fi

# Every flat-layout module + ``init.py`` (module name, not package
# init) that lives at the repo root and gets force-included by hatch.
# Keep this list in sync with the wheel target's ``force-include``
# block in pyproject.toml.
FLAT_MODULES=(
    db_ops.py
    scan.py
    search.py
    stats.py
    review.py
    init.py
    import_seed.py
    mojo_cli.py
)

echo "[dev-install] symlinking flat modules in $SITE_PKG → $REPO_ROOT"
for mod in "${FLAT_MODULES[@]}"; do
    src="$REPO_ROOT/$mod"
    dst="$SITE_PKG/$mod"
    if [[ ! -f "$src" ]]; then
        echo "  skip $mod (missing in source tree)"
        continue
    fi
    rm -f "$dst"
    ln -s "$src" "$dst"
    echo "  $mod  → $src"
done

# Sanity check: do the symlinks actually win at import time?
probe=$("$PYTHON" -c 'import scan, init, db_ops; print(scan.__file__, init.__file__, db_ops.__file__)')
expected_prefix="$REPO_ROOT"
if [[ "$probe" != *"$expected_prefix"* ]]; then
    echo "error: import still resolves outside $expected_prefix:" >&2
    echo "       $probe" >&2
    exit 1
fi

echo "[dev-install] done. live edits in $REPO_ROOT now propagate."
