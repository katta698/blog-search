#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a Lambda zip that is byte-identical when the code is identical.

    python scripts/build_zip.py <source-dir> <output.zip>

Zip files record a modification time per entry, so rebuilding an unchanged file
produces different bytes every time. Terraform hashes those bytes, so every
rebuild shows up as "source_code_hash will change" -- a diff that looks exactly
like a real code change and means nothing.

That matters more than tidiness. When a routine no-op keeps appearing in the
plan, the plan stops being read, and the one time the change is real -- a zip
built from stale source, about to roll a live function back -- it looks the
same as all the noise before it. Pinning the timestamp makes "the hash changed"
mean "the code changed", which is what makes check_lambda_drift.py worth
running.

1980-01-01 is the zero point of the zip format's date field; any fixed value
works, and this one is unambiguous about being deliberate.
"""
import os
import sys
import zipfile

EPOCH = (1980, 1, 1, 0, 0, 0)


def build(src_dir, out_path):
    files = []
    for root, dirs, names in os.walk(src_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            if n.endswith(".pyc") or n.endswith(".zip"):
                continue
            full = os.path.join(root, n)
            files.append((full, os.path.relpath(full, src_dir).replace("\\", "/")))

    # Sorted so entry order does not depend on the filesystem's whims either.
    files.sort(key=lambda p: p[1])

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in files:
            info = zipfile.ZipInfo(rel, date_time=EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as fh:
                # Normalise line endings: git hands these files out with CRLF on
                # Windows, and Lambda runs them on Linux. Without this the same
                # commit produces a different zip depending on who built it.
                data = fh.read()
            if rel.endswith((".py", ".txt", ".json", ".cfg")):
                data = data.replace(b"\r\n", b"\n")
            z.writestr(info, data)
    return len(files)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    n = build(sys.argv[1], sys.argv[2])
    print("  %s <- %d file(s) from %s" % (sys.argv[2], n, sys.argv[1]))
