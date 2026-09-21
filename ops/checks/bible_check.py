"""
ops/checks/bible_check.py — ADIC MA workbook frozen-truth verifier.

CFO directive 2026-05-24: "These figures are your bible. Every time you
finish the work, double-check everywhere that these figures are still
the same. If not, you should think and fix the problem."

Run after every change to financial code (anything in reporting/,
ledger/, billing/, or any migration that touches JE / Account). Exits
non-zero on drift > 0.5%.

Usage (on prod, inside backend container):
    sudo docker exec -i alpha-finance-backend \
        python manage.py shell -c "exec(open('/path/to/bible_check.py').read())"

Bible source: ~/.claude/CLAUDE.md (frozen numbers section), validated
against MA-June2025-ADIC-FY25.xlsx and MA-Mar2026-ADIC-9M.xlsx in
prat-skill reference. NEVER change these numbers without three explicit
CFO yeses.
"""
import sys
from datetime import date
from decimal import Decimal

from core.models import Company
from reporting.ma_pl import build_ma_pl


# (period_label, fy_start, fy_end, GWP, NEP, PAT) — all in BWP
BIBLE = {
    'FY25':    (date(2024, 7, 1), date(2025, 6, 30), 125_149_000, 52_480_000,   292_000),
    'FY26_9M': (date(2025, 7, 1), date(2026, 3, 31),  96_177_000, 42_010_000,   950_000),
}

TOLERANCE_PCT = 0.5


def run() -> int:
    """Return number of drift failures (0 = pass)."""
    c = Company.objects.filter(code__iexact='ADIC').first()
    if not c:
        print('FAIL: ADIC company not found')
        return 99
    cid = str(c.id)

    print(f'{"PERIOD":<10} {"METRIC":<6} {"LIVE":>16} '
          f'{"FROZEN":>16} {"DELTA":>12} {"%":>8}  STATUS')
    print('=' * 80)
    fail = 0
    for label, (start, end, gf, nf, pf) in BIBLE.items():
        pl = build_ma_pl(start, end, company_id=cid)
        live = {
            'GWP': Decimal(pl['totals']['gross_written_premium']),
            'NEP': Decimal(pl['totals']['net_earned_premium']),
            'PAT': Decimal(pl['totals']['pat']),
        }
        frozen = {'GWP': gf, 'NEP': nf, 'PAT': pf}
        for k in ('GWP', 'NEP', 'PAT'):
            lv = float(live[k])
            fr = frozen[k]
            delta = lv - fr
            pct = (delta / fr * 100) if fr else 0
            ok = abs(pct) < TOLERANCE_PCT
            if not ok:
                fail += 1
            status = 'OK' if ok else '*** DRIFT ***'
            print(f'{label:<10} {k:<6} {lv:>16,.0f} {fr:>16,.0f} '
                  f'{delta:>+12,.0f} {pct:>+7.2f}%  {status}')
    print()
    print('PASS' if fail == 0 else f'FAIL: {fail} line(s) drift > {TOLERANCE_PCT}%')
    return fail


if __name__ == '__main__' or True:
    sys.exit(run())
