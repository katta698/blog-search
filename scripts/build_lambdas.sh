#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$REPO_ROOT/dist"

build() {
  local name=$1
  local src="$REPO_ROOT/$name"
  local out="$DIST/$name"

  echo "==> Building $name..."
  rm -rf "$out"
  mkdir -p "$out"

  pip install -r "$(cygpath -w "$src/requirements.txt")" -t "$(cygpath -w "$out")" --quiet \
    --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12
  cp "$src/handler.py" "$out/handler.py"

  # Deterministic zip: same code in, same bytes out, so "the hash changed"
  # means "the code changed" rather than "someone rebuilt".
  python "$REPO_ROOT/scripts/build_zip.py" "$(cygpath -w "$out")" "$(cygpath -w "$DIST/$name.zip")"
  echo "    -> $DIST/$name.zip"
}

mkdir -p "$DIST"
build indexer
build query

echo ""
echo "Checking the built zips against what is actually deployed..."
python "$REPO_ROOT/scripts/check_lambda_drift.py" || {
  echo ""
  echo "Refusing to call this a successful build. Fix the above before applying."
  exit 1
}
echo ""
echo "Done. Run 'terraform apply' from terraform/ to deploy."
