"""healthcare/provider_registry.py — ADH service-provider registry import/export.

The ADH team maintains a working spreadsheet of the provider network. This
module reads that EXACT sheet into the `ServiceProvider` master and builds the
filtered Excel reports back out.

Two hard rules, from the brief + the 3-AI review (never clobber master data):
  1. Match ONLY on the AFA practice number, read as TEXT (leading zeros kept).
     Never match on name — two practices can share a name.
  2. Import is a PREVIEW then a COMMIT. `preview_import()` classifies every row
     as new / changed / unchanged / unmatched-blank and returns it for a human
     to approve; nothing is written until `commit_import()` is called with the
     rows the human approved.

No hard deletes anywhere — providers absent from a later sheet are left as-is
(deactivate is a deliberate, separate action), never deleted.
"""
from __future__ import annotations

import datetime as _dt
import io
from typing import Any

from django.db import transaction
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from .models import ServiceProvider, ServiceProviderApplication

# --- Exact spreadsheet header -> model field. Header text is matched
#     case-insensitively and whitespace-normalised so a stray trailing space
#     ("Contract Status ") or a re-cased header still lands. Invent nothing:
#     every key here is a real column from ADH's own sheet.
COLUMN_MAP: dict[str, str] = {
    "adh acceptance (ready)": "adh_acceptance",
    "practice": "practice_number",
    "discipline": "discipline",
    "prac name": "name",
    "email addr": "email",
    "town": "town",
    "contact no": "contact_number",
    "location": "location",
    "contract status": "contract_status",
    "welcome pack provided": "welcome_pack",
    "adh 'accepted here' sticker displayed": "sticker_displayed",
    "provider orientation": "provider_orientation",
    "provider onboarding link": "onboarding_link",
    "date contancted": "date_contacted",   # sheet's spelling, kept verbatim
    "comment": "comment",
    "vendor": "vendor_system",

    # --- The same two fields as THIS MODULE'S OWN EXPORT spells them. ---
    # build_export() writes "ADH Acceptance (manual)" and "'Accepted Here'
    # Sticker"; the ADH sheet above spells them "ADH Acceptance (Ready)" and
    # "ADH 'Accepted Here' Sticker Displayed". So the natural way to work —
    # export the list from Omni, edit it, upload it back — silently dropped
    # those two columns: no error, no warning, the edits simply never landed.
    # Reported 11-Sep-2026 by Keneilwe Jane, who had 212 providers accepted in
    # her sheet against 170 in Omni and could not see why.
    "adh acceptance (manual)": "adh_acceptance",
    "'accepted here' sticker": "sticker_displayed",
}

# Headers this module exports but deliberately does NOT import: three are
# derived from other fields and one is an in-system QC decision that a
# spreadsheet must never overwrite. Named here so the unrecognised-column
# warning below stays quiet about them — a warning that cries wolf is a
# warning nobody reads.
_KNOWN_NOT_IMPORTED = {
    "afa registered (derived)",
    "adh ready (derived)",
    "ready mismatch?",
    "qc confirmed",
}

# The importable text fields (everything except the derived readiness, the
# explicit QC gate, and lifecycle/provenance which are set by staff or code).
_IMPORT_FIELDS = [
    "adh_acceptance", "discipline", "name", "email", "town", "contact_number",
    "location", "contract_status", "welcome_pack", "sticker_displayed",
    "provider_orientation", "onboarding_link", "date_contacted", "comment",
    "vendor_system",
]


# The sheet's yes/no columns arrive in four spellings of two answers — on
# 21-Sep-2026 production held NO (257), Yes (17), YES (10), No (7) in the
# sticker column alone, so anything counting "YES" saw 10 of 27. Canonicalise
# on the way in, by ALLOWLIST: an answer we do not recognise is preserved
# verbatim rather than mangled into a wrong one.
_YESNO_FIELDS = ("adh_acceptance", "welcome_pack", "sticker_displayed",
                 "provider_orientation")
_YESNO_CANON = {"yes": "YES", "no": "NO", "na": "NA", "n/a": "NA"}


def canon_yesno(v: Any) -> str:
    """'Yes'/'yes'/' YES ' -> 'YES'. Anything unrecognised comes back as-is."""
    s = str(v or "").strip()
    return _YESNO_CANON.get(s.lower(), s)


def _norm_header(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def _cell(v: Any) -> str:
    """Render a cell as clean text. Numbers (e.g. a practice number Excel stored
    as a float) become plain ints, never '60178.0'."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()[:10]
    return str(v).strip()


def normalise_practice_number(v: Any) -> str:
    """Practice number as a stable TEXT key. Excel may deliver it as a float
    (60178.0) or a string with spaces; strip to the bare token but keep any
    genuine leading zeros a string carried."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_sheet(file_obj, dropped: list | None = None) -> tuple[list[dict], list[str]]:
    """Read the workbook's first sheet into a list of {field: value} dicts keyed
    by the COLUMN_MAP. Returns (rows, warnings). Rows with a blank practice
    number are dropped and counted in warnings — they cannot be matched.

    If a `dropped` list is passed, every row that does NOT make it into the
    registry is appended to it as {row, reason, name} using the row's real sheet
    number — so the loss reported 15-Sep-2026 (226 ready providers, only 218
    imported; 226 AFA numbers, only 225) is no longer silent: the user can see
    exactly which rows to fix rather than a bare count.
    """
    wb = load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return [], ["The sheet is empty."]

    # Map each column index to a model field via the header text.
    idx_to_field: dict[int, str] = {}
    for i, h in enumerate(header):
        field = COLUMN_MAP.get(_norm_header(h))
        if field:
            idx_to_field[i] = field

    warnings: list[str] = []

    # Say out loud which columns are being ignored. Silence here is what let a
    # whole column of edits disappear unnoticed for weeks (see COLUMN_MAP).
    unknown = [
        str(h).strip() for h in header
        if h is not None and str(h).strip()
        and _norm_header(h) not in COLUMN_MAP
        and _norm_header(h) not in _KNOWN_NOT_IMPORTED
    ]
    if unknown:
        warnings.append(
            f"{len(unknown)} column(s) were not recognised and will be ignored: "
            f"{', '.join(unknown[:8])}{'…' if len(unknown) > 8 else ''}."
        )

    missing = set(COLUMN_MAP.values()) - set(idx_to_field.values())
    if "practice_number" in missing:
        return [], ["No 'PRACTICE' column found — cannot import without the practice number key."]

    out: list[dict] = []
    blank_key = 0
    seen_pn: dict[str, int] = {}      # practice number -> the row it first appeared on
    dup_in_sheet = 0
    # enumerate from 2: row 1 was the header consumed above, so `rownum` is the
    # real spreadsheet row the user sees.
    for rownum, r in enumerate(rows_iter, start=2):
        rec: dict[str, str] = {}
        for i, field in idx_to_field.items():
            val = r[i] if i < len(r) else None
            if field == "practice_number":
                rec[field] = normalise_practice_number(val)
            elif field in _YESNO_FIELDS:
                rec[field] = canon_yesno(_cell(val))
            else:
                rec[field] = _cell(val)
        # skip fully-empty rows (no note recorded — a truly blank row is not a loss)
        if not any((rec.get(f) or "") for f in COLUMN_MAP.values()):
            continue
        name = rec.get("name") or rec.get("provider_name") or ""
        if not rec.get("practice_number"):
            blank_key += 1
            if dropped is not None:
                dropped.append({"row": rownum, "kind": "not_imported",
                                "reason": "no practice number", "name": name})
            continue
        pn = rec["practice_number"]
        if pn in seen_pn:
            # last occurrence wins on commit, so this line overwrites the earlier
            # one — record it so a collapsed practice number is not a silent loss.
            dup_in_sheet += 1
            if dropped is not None:
                dropped.append({
                    "row": rownum,
                    # This row IS imported — it overwrites the earlier one — so it
                    # is not a row lost from the file. Kept apart from
                    # "not_imported" so the row count cannot double-count it.
                    "kind": "overwrites_earlier",
                    "reason": f"duplicate practice number {pn} (first seen on row {seen_pn[pn]}); "
                              "the later row overwrites the earlier",
                    "name": name,
                })
        else:
            seen_pn[pn] = rownum
        out.append(rec)

    if blank_key:
        rowlist = (", rows " + ", ".join(str(d["row"]) for d in dropped if d["reason"] == "no practice number")) if dropped else ""
        warnings.append(f"{blank_key} row(s) had no practice number and were skipped{rowlist}.")
    if dup_in_sheet:
        warnings.append(f"{dup_in_sheet} row(s) repeat a practice number already in the sheet; the later row wins.")
    return out, warnings


def preview_import(file_obj) -> dict:
    """Classify every sheet row against the current registry WITHOUT writing.

    Returns {new, changed, unchanged, warnings, duplicates} where new/changed
    carry the field-level detail a human approves before commit.
    """
    dropped: list[dict] = []
    rows, warnings = parse_sheet(file_obj, dropped=dropped)
    existing = {p.practice_number: p for p in ServiceProvider.objects.all()}

    seen: dict[str, int] = {}
    for row in rows:
        seen[row["practice_number"]] = seen.get(row["practice_number"], 0) + 1
    duplicates = [k for k, n in seen.items() if n > 1]
    if duplicates:
        warnings.append(
            f"{len(duplicates)} practice number(s) appear more than once in the "
            f"sheet: {', '.join(duplicates[:8])}{'…' if len(duplicates) > 8 else ''}. "
            "The last occurrence wins."
        )

    # Classify one entry PER PRACTICE NUMBER, not per sheet row. A repeated
    # practice number is one provider written once (the last row wins, exactly
    # as commit_import writes it) — counting the rows instead reported two new
    # providers for a file that creates one, so the totals on screen did not
    # add up to the file the user uploaded.
    # Counted BEFORE the dedupe below, so "you uploaded a file with N rows"
    # stays the user's file and not our tidied version of it.
    rows_in_file = len(rows) + sum(1 for d in dropped if d.get("kind") == "not_imported")
    deduped: dict[str, dict] = {}
    for row in rows:
        deduped[row["practice_number"]] = row
    rows = list(deduped.values())

    new_rows, changed_rows, unchanged = [], [], 0
    for row in rows:
        pn = row["practice_number"]
        cur = existing.get(pn)
        if cur is None:
            new_rows.append({"practice_number": pn, "name": row.get("name", ""), "values": row})
            continue
        diffs = {}
        for f in _IMPORT_FIELDS:
            old = getattr(cur, f, "") or ""
            newv = row.get(f, "") or ""
            if old != newv:
                diffs[f] = {"old": old, "new": newv}
        if diffs:
            changed_rows.append({
                "practice_number": pn, "name": cur.name, "changes": diffs, "values": row,
            })
        else:
            unchanged += 1

    return {
        "total_rows": len(rows),
        # How many data rows the file actually held, so the screen can say
        # "you uploaded a file with N rows" and be right. `total_rows` counts
        # only what could be matched, and a duplicate appears in BOTH `rows`
        # and `dropped`, so the two cannot simply be added.
        "file_rows": rows_in_file,
        "new": new_rows,
        "changed": changed_rows,
        "unchanged": unchanged,
        "duplicates": duplicates,
        "dropped": dropped,          # every row that will NOT be imported, with sheet row + reason
        "dropped_count": len(dropped),
        "warnings": warnings,
    }


def commit_import(rows: list[dict], *, source_file: str = "", user=None) -> dict:
    """Write approved rows into the registry. `rows` is a list of the per-row
    `values` dicts from preview_import (new + changed the human accepted).

    Existing rows are updated field-by-field; missing rows are created. The
    manual `adh_acceptance` column is written through, but derived readiness is
    computed live (properties) so it is always consistent. AuditableMixin writes
    the field-level audit row on each save.
    """
    now = timezone.now()
    created, updated = 0, 0
    # All-or-nothing: a mid-row DB error (e.g. an over-length value) must not
    # leave a half-written import behind. Roll the whole batch back instead.
    with transaction.atomic():
        for row in rows:
            pn = normalise_practice_number(row.get("practice_number"))
            if not pn:
                continue
            obj = ServiceProvider.objects.filter(practice_number=pn).first()
            is_new = obj is None
            if is_new:
                obj = ServiceProvider(practice_number=pn)
            for f in _IMPORT_FIELDS:
                if f in row:
                    setattr(obj, f, row.get(f, "") or "")
            # QC is NEVER granted by the spreadsheet. A create-time seed used to
            # copy "ADH Acceptance (Ready) = YES" into qc_confirmed to give the
            # registry a day-one readiness number. The cost only became visible
            # on 21-Sep-2026: readiness then answered out of the same column it
            # was meant to check, so the tile could not disagree with the sheet
            # (222 rows said YES; 222 counted ready). Ritah Tonkope's ruling is
            # that QC "validates AFA's readiness status rather than
            # independently override it" — a validation that is auto-granted
            # validates nothing. A new provider now arrives NOT QC-confirmed and
            # surfaces in `ready_mismatch` as work to do, which is what the next
            # AFA load of ~213 providers needs. qc_confirmed is set only by a
            # person, in Omni.
            obj.source_file = source_file or obj.source_file
            obj.last_imported_at = now
            obj.save(audit_user=user, audit_description=(
                f"Imported {'new' if is_new else 'update'} from {source_file or 'spreadsheet'}"))
            created += int(is_new)
            updated += int(not is_new)
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------------------
# Exports — the four filtered reports the brief asks for.
# ---------------------------------------------------------------------------

_EXPORT_HEADERS = [
    ("practice_number", "Practice"),
    ("name", "Prac Name"),
    ("discipline", "Discipline"),
    ("town", "Town"),
    ("email", "Email Addr"),
    ("contact_number", "Contact No"),
    ("location", "Location"),
    ("contract_status", "Contract Status"),
    ("afa_registered", "AFA Registered (derived)"),
    ("welcome_pack", "Welcome Pack Provided"),
    ("sticker_displayed", "'Accepted Here' Sticker"),
    ("provider_orientation", "Provider Orientation"),
    ("qc_confirmed", "QC Confirmed"),
    ("adh_ready", "ADH Ready (derived)"),
    ("adh_acceptance", "ADH Acceptance (manual)"),
    ("ready_mismatch", "Ready mismatch?"),
    ("vendor_system", "Vendor"),
    ("comment", "Comment"),
]

FILTERS = {
    "all": lambda qs: qs,
    "afa_registered": lambda qs: [p for p in qs if p.afa_registered == "Yes"],
    "adh_ready": lambda qs: [p for p in qs if p.adh_ready],
    "registered_not_qc": lambda qs: [p for p in qs if p.afa_registered == "Yes" and not p.qc_confirmed_flag],
}

FILTER_LABELS = {
    "all": "All providers",
    "afa_registered": "AFA-registered",
    "adh_ready": "ADH-ready",
    "registered_not_qc": "Registered, not yet QC'd",
}


def _display(obj, field: str) -> Any:
    val = getattr(obj, field)
    if isinstance(val, bool):
        return "Yes" if val else "No"
    return val if val is not None else ""


def build_export(filter_key: str = "all") -> tuple[bytes, str]:
    """Build an .xlsx of the chosen filtered report. Returns (bytes, filename)."""
    if filter_key not in FILTERS:
        filter_key = "all"
    providers = ServiceProvider.objects.filter(is_active=True).order_by("name")
    rows = FILTERS[filter_key](providers)

    wb = Workbook()
    ws = wb.active
    ws.title = FILTER_LABELS[filter_key][:31]
    ws.append([label for _, label in _EXPORT_HEADERS])
    for obj in rows:
        ws.append([_display(obj, f) for f, _ in _EXPORT_HEADERS])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = timezone.now().strftime("%Y-%m-%d")
    fname = f"ADH_service_providers_{filter_key}_{stamp}.xlsx"
    return buf.getvalue(), fname


def dashboard_counts() -> dict:
    """Readiness summary for the dashboard tiles — computed in Python because
    readiness is derived (not a DB column)."""
    providers = list(ServiceProvider.objects.filter(is_active=True))
    by_discipline: dict[str, int] = {}
    for p in providers:
        d = (p.discipline or "—").upper()
        by_discipline[d] = by_discipline.get(d, 0) + 1
    return {
        "total": len(providers),
        "afa_registered": sum(1 for p in providers if p.afa_registered == "Yes"),
        "afa_pending": sum(1 for p in providers if p.afa_registered == "Pending"),
        "adh_ready": sum(1 for p in providers if p.adh_ready),
        # AFA's own readiness claim, counted straight off their column and kept
        # BESIDE our derived number rather than blended into it. Two numbers
        # that agree are evidence; one number that cannot disagree is not.
        "afa_says_ready": sum(1 for p in providers
                              if (p.adh_acceptance or "").strip().upper() == "YES"),
        "registered_not_qc": sum(1 for p in providers if p.afa_registered == "Yes" and not p.qc_confirmed_flag),
        "mismatches": sum(1 for p in providers if p.ready_mismatch),
        "pending_applications": ServiceProviderApplication.objects.filter(status="pending").count(),
        "by_discipline": dict(sorted(by_discipline.items(), key=lambda kv: -kv[1])),
    }
