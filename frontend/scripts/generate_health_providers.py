#!/usr/bin/env python3
"""Generate frontend/src/lib/healthProviders.ts from the Registered Providers register.

    python3 frontend/scripts/generate_health_providers.py <register.xlsx>

WHY THIS EXISTS: the sister file `lib/healthCover.ts` carries ~60 benefit limits
that were typed in BY HAND, which is exactly why the cover page still shows a
"not yet checked" banner. The provider list is not going the same way — it is
generated, and this is the generator. A generated file without its generator is
unauditable: the drop rules survive only in a commit message and the next
refresh becomes archaeology (checklist H28).

THE SOURCE FILE IS NOT COMMITTED. It carries 197 personal gmail / yahoo /
hotmail addresses belonging to individual doctors; those must not sit in git
history forever. Keep the register in Omni's file vault and point this script
at a local copy.

PROVENANCE OF THE CURRENT FILE
    source   : "Registered Providers- 13.07.26.xlsx"  (Steven Diaz, 2026-08-09)
    sha256   : 2efad56661cc5e234725849a3db4f82cdbb0d20e4c7637b5baadb16bc806bedf
    rows in  : 248
    rows out : 247
    dropped  : 1 — a stray junk row carrying only the number "246" in the
               discipline column, with no practice name and no town. NOTE: the
               register does contain a repeated practice NUMBER, but those are
               two genuinely different practices in different towns, so both are
               kept. An earlier commit message wrongly claimed two drops.

WHAT IS DELIBERATELY NOT CARRIED OVER: the "Email Addr" column. 197 of the 248
rows are an individual doctor's personal address. The page is intended for the
public. A member needs the practice, the discipline and where it is.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

# Registry-speak -> what a member would actually call it.
DISCIPLINE_LABEL = {
    'GP': 'General Practitioner',
    'LABS': 'Laboratory',
    'PHYSIOTHERAPISTS': 'Physiotherapy',
    'RADIOGRAPHY': 'Radiography',
    'APPROVED T U/ DAY CLINICS': 'Day Clinic',
}

HEADER = '''/**
 * frontend/src/lib/healthProviders.ts — Alpha Direct Health network providers.
 *
 * GENERATED — do not hand-edit. Regenerate with:
 *     python3 frontend/scripts/generate_health_providers.py <register.xlsx>
 *
 * Source: "Registered Providers 13.07.26", supplied by Steven Diaz 2026-08-09.
 * Generated, not re-typed, so there is no transcription risk of the kind that
 * put the "not yet checked" banner on the cover page.
 *
 * PRACTITIONER EMAIL ADDRESSES ARE DELIBERATELY NOT INCLUDED. 197 of the 248
 * source rows carry a personal gmail / yahoo / hotmail address belonging to an
 * individual doctor. This page is DESIGNED for public use, and publishing a
 * doctor's private address is not ours to do. A member needs the practice, the
 * discipline and where it is — which is what is here.
 *
 * NOTE ON "public": the page is designed for public use but is NOT public yet —
 * it sits inside the Omni dashboard behind SSO. Taking it public is a separate,
 * deliberate decision with its own security posture. Do not read "public" off
 * this comment when deciding what data is safe to add.
 */
export interface Provider { n: string; d: string; t: string; l: string }

/** name · discipline · town · location */
export const PROVIDERS: Provider[] = '''


def _clean(v) -> str:
    return ('' if v is None else str(v)).strip()


def _title(s: str) -> str:
    s = re.sub(r'\s+', ' ', s).strip()
    return s.title() if (s.isupper() or s.islower()) else s


def build(src: Path) -> tuple[list[dict], int, list[str]]:
    import openpyxl

    ws = openpyxl.load_workbook(src, data_only=True)['Sheet1']
    rows, dropped = [], []
    total = 0

    for r in ws.iter_rows(min_row=2, values_only=True):
        if not any(c not in (None, '') for c in r):
            continue
        total += 1
        discipline = _clean(r[1]).upper()
        practice   = _title(_clean(r[7]))
        town       = _title(_clean(r[5]))

        if not practice or not town:
            dropped.append(f'no practice name or town: {_clean(r[0]) or "(no prac num)"}')
            continue
        if discipline.isdigit():
            # One row carries "246" in the discipline column — a capture error.
            dropped.append(f'discipline is a number ({discipline}): {practice}')
            continue

        rows.append({
            'n': practice,
            'd': _title(DISCIPLINE_LABEL.get(discipline, discipline)),
            't': town,
            'l': re.sub(r'\s+', ' ', _clean(r[6])),
        })

    rows.sort(key=lambda x: (x['t'], x['d'], x['n']))

    seen, out = set(), []
    for x in rows:
        key = (x['n'].lower(), x['t'].lower())
        if key in seen:
            dropped.append(f'duplicate practice in the same town: {x["n"]} ({x["t"]})')
            continue
        seen.add(key)
        out.append(x)

    return out, total, dropped


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).expanduser()
    if not src.exists():
        print(f'not found: {src}')
        return 2

    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    providers, total, dropped = build(src)

    target = Path(__file__).resolve().parents[1] / 'src' / 'lib' / 'healthProviders.ts'
    target.write_text(HEADER + json.dumps(providers, ensure_ascii=False, indent=0) + '\n',
                      encoding='utf-8')

    print(f'source   : {src.name}')
    print(f'sha256   : {digest}')
    print(f'rows in  : {total}')
    print(f'rows out : {len(providers)}')
    print(f'dropped  : {len(dropped)}')
    for d in dropped:
        print(f'    - {d}')
    print(f'towns    : {len({p["t"] for p in providers})}')
    print(f'wrote    : {target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
