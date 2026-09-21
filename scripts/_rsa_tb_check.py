"""
Verify RSA TB reset worked.

Original: JE-RSA-2026-000001 — FY25 TB mis-posted at 30-Jun-2026 (FY26),
amount BWP 26,622,704.66, claimed REVERSED via JE-RSA-2026-000001-REV.

Need to confirm:
  1) Both JEs exist + status posted
  2) Per-account net debit minus credit across the pair sums to zero
  3) FY26 RSA TB endpoint returns zero balances on the affected accounts
"""
import builtins, json
from decimal import Decimal
from collections import defaultdict
from ledger.models import JournalEntry, JournalEntryLine
from core.models import Company

BE = builtins.BaseException
RSA = None
try:
    RSA = Company.objects.filter(code__icontains='RSA').first() or \
          Company.objects.filter(name__icontains='RSA').first()
    print(f'RSA company: {RSA} (id={RSA.id if RSA else None})')
except BE as e:
    print('company lookup err:', e)

# 1) Find the two JEs
orig = JournalEntry.objects.filter(entry_number='JE-RSA-2026-000001').first()
rev  = JournalEntry.objects.filter(entry_number='JE-RSA-2026-000001-REV').first()
print(f'\nORIG: {orig and (orig.entry_number, orig.status, orig.entry_date, orig.company_id)}')
print(f'REV : {rev  and ( rev.entry_number,  rev.status,  rev.entry_date,  rev.company_id)}')

if not orig:
    print('!! ORIGINAL JE NOT FOUND — investigation paths:')
    near = JournalEntry.objects.filter(entry_number__icontains='RSA-2026-0000')[:10]
    for j in near:
        print('  ', j.entry_number, j.status, j.entry_date)

# 2) Per-account net for the pair
def lines_sum(je):
    if not je: return {}
    out = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    for L in JournalEntryLine.objects.filter(entry=je).select_related('account'):
        code = getattr(L.account, 'code', '?')
        out[code][0] += L.debit_bwp or Decimal('0')
        out[code][1] += L.credit_bwp or Decimal('0')
    return dict(out)

orig_s = lines_sum(orig)
rev_s  = lines_sum(rev)
print(f'\norig has {len(orig_s)} accounts, rev has {len(rev_s)} accounts')

# Combine
all_codes = set(orig_s) | set(rev_s)
print('\nPer-account NET (orig+rev) debit/credit (BWP) — first 20 non-zero:')
nonzero = 0
for c in sorted(all_codes):
    d = (orig_s.get(c, [Decimal('0'), Decimal('0')])[0] +
         rev_s .get(c, [Decimal('0'), Decimal('0')])[0])
    cr = (orig_s.get(c, [Decimal('0'), Decimal('0')])[1] +
          rev_s .get(c, [Decimal('0'), Decimal('0')])[1])
    net = d - cr
    if net != 0:
        nonzero += 1
        if nonzero <= 20:
            print(f'  {c}  D={d}  C={cr}  NET={net}')
print(f'TOTAL accounts with non-zero NET across the pair: {nonzero}')

# 3) FY26 RSA TB endpoint — pull live, look at non-zero RSA balances in FY26
print('\n--- Live FY26 RSA TB lookup ---')
try:
    from reporting.reports import trial_balance
    if RSA:
        import datetime as dt
        tb = trial_balance(
            company=RSA,
            as_of_date=dt.date(2026, 6, 30),
            fiscal_year_start=dt.date(2025, 7, 1),
        )
        rows = tb.get('rows') or tb.get('lines') or tb
        if isinstance(rows, list):
            nz = [r for r in rows
                  if abs(Decimal(str(r.get('closing_debit',0)
                                    or r.get('debit_bwp',0)
                                    or r.get('debit',0)
                                    or 0))) > 0
                  or abs(Decimal(str(r.get('closing_credit',0)
                                    or r.get('credit_bwp',0)
                                    or r.get('credit',0)
                                    or 0))) > 0]
            print(f'TB rows with non-zero balance: {len(nz)} (of {len(rows)})')
            for r in nz[:15]:
                print(f'  {r.get("account_code") or r.get("code")}'
                      f'  {r.get("account_name") or r.get("name")}'
                      f'  D={r.get("closing_debit") or r.get("debit_bwp") or r.get("debit")}'
                      f'  C={r.get("closing_credit") or r.get("credit_bwp") or r.get("credit")}')
        else:
            print('tb shape:', type(rows).__name__, list(tb.keys()) if isinstance(tb, dict) else '')
except BE as e:
    print('tb lookup err:', type(e).__name__, str(e)[:200])

# 4) Whole-FY26 RSA — sum every posted JE line for RSA in 2025-07-01..2026-06-30
print('\n--- Aggregate RSA JE lines in FY26 (posted only) ---')
if RSA:
    import datetime as dt
    qs = JournalEntryLine.objects.filter(
        entry__company=RSA,
        entry__status='posted',
        entry__entry_date__gte=dt.date(2025, 7, 1),
        entry__entry_date__lte=dt.date(2026, 6, 30),
    ).values_list('entry__entry_number', 'account__code',
                  'debit_bwp', 'credit_bwp', 'entry__entry_date')
    by_code = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    by_je   = defaultdict(int)
    for en, code, d, c, ed in qs:
        by_code[code][0] += d or Decimal('0')
        by_code[code][1] += c or Decimal('0')
        by_je[en] += 1
    print(f'distinct JEs posted to RSA in FY26: {len(by_je)}')
    for en, n in list(by_je.items())[:15]:
        print(f'  {en}: {n} lines')
    nz = [(c, d, cr, d-cr) for c, (d, cr) in by_code.items() if (d - cr) != 0]
    print(f'\naccounts with non-zero NET in RSA-FY26 across ALL JEs: {len(nz)}')
    for c, d, cr, n in sorted(nz, key=lambda r: -abs(r[3]))[:20]:
        print(f'  {c}  D={d}  C={cr}  NET={n}')
