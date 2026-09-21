# ADI vs ADIC — entity disambiguation

**Date:** 2026-05-16
**Decision-maker:** CFO (Prathap)
**Implemented by:** Claude Code

## Context

Two `core.Company` rows existed both labelled "Alpha Direct Insurance":

| Code | Display name (before) | JEs | Rev FY26 YTD | Exp FY26 YTD | NetPL | Source |
|------|-----------------------|-----|--------------|--------------|-------|--------|
| ADIC | Alpha Direct Insurance | 35,619 | 277.4 M | 276.1 M | **+1.30 M** ✓ matches MA | Odoo company_id=4 (operational carrier) |
| ADI  | Alpha Direct Insurance | 22,909 | 70.3 M  |  95.3 M | −25.0 M (doesn't match MA) | Odoo legacy company mapping pre-restructure |

Both rows referred to the same legal entity. ADIC is the live insurance carrier; its GL postings reconcile to the CFO's Management Accounts pack (PAT ≈ 0.95 M for Jul 2025 – Mar 2026, with the residual 8 M variance traced to missing GL codes in `reporting/ma_pl_spec.py` — see `project_ma_pl_8mn_gap_root_cause_2026_05_15.md`).

The ADI row carried 22,909 JEs that came across in the Odoo migration from a *different* Odoo `company_id` representing a pre-restructure entity that no longer transacts. The numbers don't roll up to either MA or audited financials and were appearing in the company switcher as a confusing duplicate.

## Decision

**Soft-delete ADI.** Rename to "Alpha Direct Insurance (legacy — superseded by ADIC)" and set `is_active=False`. The Company row and all 22,909 JEs stay in the database for audit; the active = False filter hides ADI from the topbar company picker and from any UI list that filters by `is_active=True`.

This is reversible: setting `is_active=True` brings it back in one row.

## Why not hard-delete

- 22,909 JEs is too much data to lose without a stronger case for it.
- Audit trail: keeping the rows preserves the ability to investigate a historical question by switching the row active again.
- The Company FK on `JournalEntry` is `on_delete=PROTECT`, so a hard delete would have failed without first re-attaching or nuking every dependent JE.

## Why not merge into ADIC

ADI's numbers don't reconcile to ADIC's, so they're not duplicates of the same transactions across two Odoo company_ids — they're a *different* historical entity's books. Merging would double-count the overlapping period and corrupt the audited ADIC totals.

## Implementation

Single Django shell write (no migration, no schema change):

```python
from core.models import Company
adi = Company.objects.get(code__iexact='ADI')
adi.name = 'Alpha Direct Insurance (legacy — superseded by ADIC)'
adi.is_active = False
adi.save(update_fields=['name', 'is_active'])
```

Ran against prod on 2026-05-16 via SSM. ADIC untouched.

## Verification

After the soft-delete the topbar company picker should list ADIC but not ADI. Default selection (per `CompanyContext.PREFERRED_DEFAULT_CODES = ['ADIC', 'ADI']`) still picks ADIC first; the ADI fallback would now be ignored because the cached company list won't include inactive rows.
