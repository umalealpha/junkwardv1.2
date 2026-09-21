#!/usr/bin/env python3
"""
audit_company_param.py — find pages/endpoints that can hit the SAME bug the
Intelligence Summary page hit on 2026-07-28.

THE BUG CLASS
-------------
The frontend's apiFetch() auto-injects the globally selected company on every
GET as its UUID *id*:  ?company=<uuid>  (from localStorage 'alpha_company_id').
Any backend GET handler that reads that `company` query param and then resolves
the Company by its *code* (e.g. Company.objects.filter(code__iexact=company))
will NOT match the UUID, and either errors ("Unknown company code: <uuid>") or
silently returns nothing. That is exactly what broke /intel-summary.

WHAT THIS DOES
--------------
Pure static analysis of the backend source (no DB, no login). For each Python
function it flags two things:

  HIGH  — the function BOTH reads `company` from the request query params AND
          resolves a Company by `code` in the same function, WITHOUT also
          accepting the id / using the safe resolve_company() helper. This is a
          near-certain repeat of the intel-summary bug.

  REVIEW — the function reads `company` from the query params but resolves it
           somewhere else (a helper/service). Listed so a human can confirm the
           helper accepts the UUID id, because a cross-function case (like the
           original intel-summary bug) can't be proven safe by this file alone.

Exit code is non-zero if any HIGH findings exist, so this can gate CI later.
"""
from __future__ import annotations

import ast
import os
import re
import sys

# Reads the company from the request query string (what apiFetch injects into).
_READS_COMPANY_QP = re.compile(
    r"""query_params\s*\.\s*get\(\s*['"]company['"]"""
    r"""|\.GET\s*\.\s*get\(\s*['"]company['"]""",
    re.VERBOSE,
)
# Resolves a Company by its human code (the unsafe half).
_CODE_LOOKUP = re.compile(
    r"""Company\.objects\.(?:filter|get)\([^)]*\bcode(?:__i?exact)?\s*=""",
    re.VERBOSE,
)
# Signals the function ALSO handles the UUID id, so it is NOT broken.
_ID_SAFE = re.compile(
    r"""resolve_company\s*\("""            # the shared safe helper
    r"""|Company\.objects\.(?:filter|get)\([^)]*\b(?:id|pk|uuid)\b\s*="""
    r"""|Q\(\s*id\s*="""                    # Q(id=…) OR-branch
    r"""|uuid\.UUID\s*\("""                 # the "try uuid.UUID(x); use as-is" idiom
    r"""|allowed_company_ids""",            # id-based access gate
    re.VERBOSE,
)

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
             "migrations", "frontend", ".next", "static", "media"}


def iter_py(root: str):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                yield os.path.join(dirpath, f)


def scan_file(path: str, root: str):
    high, review = [], []
    try:
        src = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        return high, review
    if "company" not in src:               # cheap early-out
        return high, review
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return high, review

    rel = os.path.relpath(path, root).replace("\\", "/")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if not _READS_COMPANY_QP.search(seg):
            continue
        entry = f"{rel}:{node.lineno}  {node.name}()"
        if _CODE_LOOKUP.search(seg):
            if _ID_SAFE.search(seg):
                continue                    # reads + code-lookup BUT id-safe → fine
            high.append(entry)
        else:
            # Reads the param but resolves elsewhere — safe only if the helper
            # it calls handles the id. Flag unless it visibly uses the helper.
            if not _ID_SAFE.search(seg):
                review.append(entry)
    return high, review


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    all_high, all_review = [], []
    for path in iter_py(root):
        h, r = scan_file(path, root)
        all_high += h
        all_review += r

    print("=" * 72)
    print("COMPANY-PARAM AUDIT  (company code-vs-UUID mismatch, the intel-summary class)")
    print("=" * 72)
    print(f"\nHIGH — reads ?company= and resolves by CODE only ({len(all_high)}):")
    if all_high:
        for e in sorted(all_high):
            print("  ✗ " + e)
    else:
        print("  (none — no other endpoint repeats the intel-summary bug in one function)")

    print(f"\nREVIEW — reads ?company= but resolves elsewhere; confirm the helper "
          f"takes the id ({len(all_review)}):")
    if all_review:
        for e in sorted(all_review):
            print("  • " + e)
    else:
        print("  (none)")

    print("\n" + "-" * 72)
    print(f"RESULT: {len(all_high)} high, {len(all_review)} to review.")
    sys.exit(1 if all_high else 0)


if __name__ == "__main__":
    main()
