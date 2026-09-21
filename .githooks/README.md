# .githooks — the checks that cannot be forgotten

`git config core.hooksPath .githooks` — **run this once in every clone**, on every
machine. Do it now if you have just cloned.

## Why this exists

On 9 August 2026 four small changes took four hours, and about half of that was
rework. **Six of the eight mistakes were already written on a checklist that
nobody read.** A control enforced by memory is a wish, not a control.

`pre-commit` runs `prat-skill/e2e/preflight.sh` against exactly what you are
about to commit. It refuses the commit on:

* a literal credential in the diff (H8)
* a write that stamps a company without clamping it to the caller's allowed
  companies (H33) — the fault that let a quotation land on another company's books
* an exception caught and dropped, **including inside test code** (H6)
* a real colleague's name in a fixture (H19 / C5)
* a new setting that would never reach the container (H24)

and warns on: frozen ADIC figures touched (C4) · a view with no
`permission_classes` (H5) · a financial record exposed as full CRUD (H21) · a
frontend change the backend-only deploy would leave stale (C2/C3) · being behind
the base branch (H2) · models changed without a migration check (C1) · guards
that skip instead of throwing · new tests not yet proven to fail without their fix.

## Escape hatch

`PREFLIGHT_SKIP=1 git commit ...` — and **say in the commit message that the
checks did not run**. If the script is missing the hook fails loudly rather than
letting the commit through quietly; that is deliberate.

## Extending it

Add to `preflight.sh`, not to the number of reviewers. On 8 Aug three AI reviewers
produced 26 "critical" findings of which **11 were the same wrong claim**, all
disproved by one `makemigrations --check`. Machines settle facts; review is for
judgement.
