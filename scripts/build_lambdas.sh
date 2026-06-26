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

  python -c "
import zipfile, os, sys
out = sys.argv[1]
dst = sys.argv[2]
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(out):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if not f.endswith('.pyc'):
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, out))
" "$(cygpath -w "$out")" "$(cygpath -w "$DIST/$name.zip")"
  echo "    -> $DIST/$name.zip"
}

mkdir -p "$DIST"
build indexer
build query

echo ""
echo "Done. Run 'terraform apply' from terraform/ to deploy."
