#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refuse to let `terraform apply` roll a Lambda back.

Run this before every apply:

    python scripts/check_lambda_drift.py

Terraform deploys each function from a zip under dist/, and decides whether to
deploy at all by comparing that zip's hash to the one in state. Neither of those
things knows what is actually running in AWS. So a zip that is older than the
deployed function is not a conflict Terraform can see -- it is just a different
hash, and applying quietly replaces live code with the older copy.

That is not hypothetical. On 2026-08-12 dist/query.zip was dated 23 July while
the deployed blog-search-query carried the /summary feature added on the 24th,
because that change had been built and deployed straight from a working copy
and never committed. A routine apply would have removed At a glance from all
113 posts, and nothing would have failed: the plan shows only "source_code_hash
will change", which is what a legitimate deploy looks like too.

The check is therefore not "do the hashes match" -- they will not, routinely and
harmlessly. It is "does the deployed function contain code that the local source
does not". That is the only difference that means data loss.

Exit codes:
    0  safe to apply (identical, or the local source is ahead)
    1  the deployed function has code the local source lacks -- applying
       would delete it
"""
import base64
import difflib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# function name -> (source directory, zip built from it)
FUNCTIONS = {
    "blog-search-query": ("query", "dist/query.zip"),
    "blog-search-indexer": ("indexer", "dist/indexer.zip"),
}

problems = []
notes = []


def aws(*args):
    """Shell out to the CLI rather than importing boto3.

    The CLI is already installed and already holds the SSO session; requiring
    boto3 would mean this check fails to run in exactly the situation where
    skipping it is most tempting.
    """
    out = subprocess.run(
        ["aws"] + list(args), capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "aws call failed")
    return out.stdout.strip()


def normalise(raw):
    """Compare content, not line endings.

    Git checks these files out with CRLF on Windows while the zip and the
    deployed copy carry LF. Without this every file looks changed and the
    check cries wolf until someone stops running it.
    """
    return raw.decode("utf-8", "replace").replace("\r\n", "\n")


def deployed_package(name):
    """Fetch what is actually running: the handler source AND the file list.

    The file list is not a detail. An early version of this script compared
    handler.py alone and pronounced a zip "identical to deployed" while that
    zip was missing every bundled dependency -- the whole of requests and bs4.
    Applying it took blog-search-indexer down with ImportModuleError, and this
    check said it was safe right up until it wasn't.
    """
    url = aws("lambda", "get-function", "--function-name", name,
              "--query", "Code.Location", "--output", "text")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "live.zip")
        urllib.request.urlretrieve(url, path)
        with zipfile.ZipFile(path) as z:
            return normalise(z.read("handler.py")), set(z.namelist())


def check(name, src_dir, zip_rel):
    zip_path = os.path.join(ROOT, zip_rel)
    src_path = os.path.join(ROOT, src_dir, "handler.py")

    if not os.path.isfile(zip_path):
        problems.append(
            "%s: %s does not exist. terraform apply will fail on it -- run "
            "scripts/build_lambdas.sh first." % (name, zip_rel))
        return
    if not os.path.isfile(src_path):
        problems.append("%s: %s/handler.py is missing" % (name, src_dir))
        return

    local_src = normalise(io.open(src_path, "rb").read())
    zipped_src = normalise(zipfile.ZipFile(zip_path).read("handler.py"))

    # 1. Is the zip even built from the current source? A stale zip is how the
    #    wrong code gets deployed in the first place.
    if zipped_src != local_src:
        problems.append(
            "%s: %s was built from a different version of %s/handler.py. "
            "Rebuild it before applying, or you will deploy code that is not "
            "the code you just edited." % (name, zip_rel, src_dir))
        return

    # 2. The hash Terraform compares against, computed the way Terraform's
    #    filebase64sha256() does, so an identical result means apply is a no-op
    #    for this function.
    local_hash = base64.b64encode(
        hashlib.sha256(io.open(zip_path, "rb").read()).digest()).decode()
    live_hash = aws("lambda", "get-function-configuration",
                    "--function-name", name,
                    "--query", "CodeSha256", "--output", "text")
    if local_hash == live_hash:
        notes.append("%s: identical to deployed, apply is a no-op" % name)
        return

    # 3. The hashes differ, which is normal and usually fine. The question that
    #    matters is the direction: does the running function contain anything
    #    the local zip does not?
    live_src, live_files = deployed_package(name)
    local_files = set(zipfile.ZipFile(zip_path).namelist())

    # 3a. Dependencies first. A missing module does not show up as a code
    #     difference at all -- handler.py can match perfectly while the zip is
    #     unrunnable, which is exactly how the indexer was broken.
    missing = live_files - local_files
    if missing:
        sample = sorted(missing)[:5]
        problems.append(
            "%s: the deployed package contains %d file(s) that %s does not, "
            "including bundled dependencies. Deploying this would remove them "
            "and the function would fail at import.\n"
            "        Rebuild with dependencies (see scripts/build_lambdas.sh), "
            "not by zipping handler.py alone.\n"
            "        Examples: %s"
            % (name, len(missing), zip_rel, ", ".join(sample)))
        return

    if live_src == local_src:
        notes.append(
            "%s: same code and same file list, different zip bytes. "
            "Applying redeploys identical code." % name)
        return

    lost = [ln for ln in difflib.unified_diff(
        live_src.splitlines(), local_src.splitlines(), lineterm="")
        if ln.startswith("-") and not ln.startswith("---")]
    gained = [ln for ln in difflib.unified_diff(
        live_src.splitlines(), local_src.splitlines(), lineterm="")
        if ln.startswith("+") and not ln.startswith("+++")]

    if lost:
        problems.append(
            "%s: the DEPLOYED function has %d line(s) that %s/handler.py does "
            "not. Applying would delete them.\n"
            "        This usually means someone deployed a change without "
            "committing it.\n"
            "        Recover it first:  aws lambda get-function "
            "--function-name %s --query Code.Location --output text\n"
            "        First few lines that would be lost:\n%s"
            % (name, len(lost), src_dir, name,
               "\n".join("          %s" % l for l in lost[:5])))
    else:
        notes.append(
            "%s: local source is ahead by %d line(s), nothing would be lost"
            % (name, len(gained)))


def main():
    for name, (src_dir, zip_rel) in sorted(FUNCTIONS.items()):
        try:
            check(name, src_dir, zip_rel)
        except Exception as exc:
            problems.append("%s: could not check (%s)" % (name, exc))

    for n in notes:
        print("ok    %s" % n)
    for p in problems:
        print("ERROR %s" % p)

    if problems:
        print("\n%d problem(s). Do NOT run terraform apply until these are "
              "resolved." % len(problems))
        return 1
    print("\nSafe to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
