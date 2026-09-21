"""
payroll/management/commands/import_adic_payroll.py

Load a month's payroll FIGURES from the HR salary-recon workbook into omni's
payroll register. CFO directive 2026-06-18: keep the confidential payroll in
omni (parsed into the payroll module). ALPHA DIRECT INSURANCE (ADIC) ONLY.

What it does (and deliberately does NOT do):
  * Creates/gets the PayrollPeriod (status OPEN — NOT posted) and upserts one
    Payslip per matched employee with the workbook's OWN computed Gross / PAYE /
    Net / CTC (status=draft, company=ADIC).
  * Does NOT post to the GL (period.journal_entry stays NULL; no JE), does NOT
    re-derive PAYE (the workbook figures are authoritative — kept exactly), and
    does NOT touch any other entity.

Columns are read BY HEADER NAME from the May sheet:
  Employee | Gross | PAYE | Net Salary | CTC

Excel password from the PW env var. Dry-run by default; --commit writes.
"""
from __future__ import annotations

import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company
from payroll.models import Employee, PayrollPeriod, Payslip

ZERO = Decimal("0.00")


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace(".", " ")).strip().lower()


def _first_last(s):
    p = _norm(s).split()
    return (p[0], p[-1]) if p else None


def _dec(v) -> Decimal:
    if v is None or str(v).strip() == "":
        return ZERO
    try:
        return Decimal(str(v).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return ZERO


def _load_wb(path, pw):
    """Open the workbook. Tries a plain (already-decrypted) read first so the
    prod container needs only openpyxl; falls back to msoffcrypto when the file
    is still password-protected (requires msoffcrypto + PW, dev-side only)."""
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — encrypted OLE container, decrypt below
        import io
        import msoffcrypto
        dec = io.BytesIO()
        with open(path, "rb") as f:
            off = msoffcrypto.OfficeFile(f)
            off.load_key(password=pw)
            off.decrypt(dec)
        dec.seek(0)
        return openpyxl.load_workbook(dec, read_only=True, data_only=True)


class Command(BaseCommand):
    help = "Import a month's ADIC payroll figures into the payroll register (no GL)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to the (encrypted) .xlsx")
        parser.add_argument("--sheet", default="Payroll Report.xlsx", help="Worksheet name")
        parser.add_argument("--period", required=True, help="Period name, e.g. 2026-05")
        parser.add_argument("--start", required=True, help="Period start YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="Period end YYYY-MM-DD")
        parser.add_argument("--pay-date", default="", help="Pay date YYYY-MM-DD (optional)")
        parser.add_argument("--commit", action="store_true", help="Write. Without it, dry-run.")

    # -- helpers ------------------------------------------------------------
    def _adic(self) -> Company:
        c = (Company.objects.filter(code__iexact="ADIC").first()
             or Company.objects.filter(name__icontains="Alpha Direct Insurance")
                                .exclude(name__icontains="Instant").first())
        if not c:
            raise CommandError("Could not resolve the ADIC company (code ADIC / 'Alpha Direct Insurance').")
        return c

    def _employee_index(self):
        by_norm, by_fl = {}, {}
        for e in Employee.objects.select_related("company").all():
            by_norm.setdefault(_norm(e.full_name), []).append(e)
            fl = _first_last(e.full_name)
            if fl:
                by_fl.setdefault(fl, []).append(e)
        return by_norm, by_fl

    def _match(self, name, idx, adic):
        """Resolve a workbook name -> Employee, preferring ADIC then unassigned."""
        by_norm, by_fl = idx
        cands = by_norm.get(_norm(name)) or by_fl.get(_first_last(name) or ("", "")) or []
        if not cands:
            return None, "not found"
        if len(cands) == 1:
            return cands[0], None
        # disambiguate: prefer ADIC, then no-company (the M365-unassigned imports)
        adic_hits = [c for c in cands if c.company_id == adic.id]
        if len(adic_hits) == 1:
            return adic_hits[0], None
        null_hits = [c for c in cands if c.company_id is None]
        if not adic_hits and len(null_hits) == 1:
            return null_hits[0], None
        return None, f"ambiguous ({len(cands)} matches)"

    # -- main ---------------------------------------------------------------
    def handle(self, *args, **o):
        wb = _load_wb(o["file"], os.environ.get("PW", ""))
        if o["sheet"] not in wb.sheetnames:
            raise CommandError(f"Sheet {o['sheet']!r} not found. Sheets: {wb.sheetnames}")
        rows = list(wb[o["sheet"]].iter_rows(values_only=True))

        # find the header row (the one containing an 'Employee' label)
        hi = next((i for i, r in enumerate(rows)
                   if any(isinstance(c, str) and c.strip().lower() == "employee" for c in r)), None)
        if hi is None:
            raise CommandError("Could not find the 'Employee' header row.")
        hdr = [str(c).strip().lower() if c else "" for c in rows[hi]]

        def col(*names):
            for n in names:
                if n in hdr:
                    return hdr.index(n)
            return None

        ci_name = col("employee")
        ci_gross = col("gross")
        ci_paye = col("paye")
        ci_net = col("net salary", "net", "net pay")
        ci_ctc = col("ctc")
        if None in (ci_name, ci_gross, ci_paye, ci_net):
            raise CommandError(f"Missing a required column. Found headers: {[h for h in hdr if h]}")

        adic = self._adic()
        idx = self._employee_index()

        parsed, unmatched, ambig = [], [], []
        tg = tp = tn = tc = ZERO
        for r in rows[hi + 1:]:
            if ci_name >= len(r):
                continue
            name = r[ci_name]
            if not name or not str(name).strip():
                continue
            nm = str(name).strip()
            if nm.lower() in ("total", "totals", "grand total"):
                continue
            emp, reason = self._match(nm, idx, adic)
            # Workbook shows PAYE as a negative deduction; omni's Payslip.paye_amount
            # is the positive tax (net = gross - paye - deductions). Store magnitude.
            g, p, n = _dec(r[ci_gross]), abs(_dec(r[ci_paye])), _dec(r[ci_net])
            c = _dec(r[ci_ctc]) if ci_ctc is not None and ci_ctc < len(r) else ZERO
            if emp is None:
                (ambig if reason and reason.startswith("ambig") else unmatched).append(f"{nm} ({reason})")
                continue
            parsed.append((emp, g, p, n, c))
            tg += g; tp += p; tn += n; tc += c

        self.stdout.write(f"Sheet rows below header : {len(rows) - hi - 1}")
        self.stdout.write(f"Matched employees       : {len(parsed)}")
        self.stdout.write(f"Unmatched               : {len(unmatched)}  {unmatched}")
        self.stdout.write(f"Ambiguous               : {len(ambig)}  {ambig}")
        self.stdout.write(f"AGGREGATE Gross={tg}  PAYE={tp}  Net={tn}  CTC={tc}  (reconcile to workbook totals)")

        if not o["commit"]:
            self.stdout.write(self.style.WARNING("DRY-RUN — nothing written. Re-run with --commit."))
            return

        start = date.fromisoformat(o["start"])
        end = date.fromisoformat(o["end"])
        pay = date.fromisoformat(o["pay_date"]) if o["pay_date"] else None
        with transaction.atomic():
            period, _ = PayrollPeriod.objects.get_or_create(
                period_name=o["period"],
                defaults={"start_date": start, "end_date": end, "pay_date": pay,
                          "status": PayrollPeriod.Status.OPEN,
                          "notes": "ADIC May 2026 — figures loaded from HR salary recon (no GL)."},
            )
            n_new = n_upd = 0
            for emp, g, p, net, c in parsed:
                obj, created = Payslip.objects.update_or_create(
                    employee=emp, period=period,
                    defaults={"company": adic, "gross_amount": g, "paye_amount": p,
                              "net_amount": net, "ctc_amount": c,
                              "status": Payslip.Status.DRAFT,
                              "notes": "Loaded from HR May 2026 salary recon (figures kept as-is)."},
                )
                n_new += int(created); n_upd += int(not created)
        self.stdout.write(self.style.SUCCESS(
            f"Period {period.period_name} ({period.status}, GL JE={period.journal_entry_id}). "
            f"Payslips created={n_new} updated={n_upd}."))
