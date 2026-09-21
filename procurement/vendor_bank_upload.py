"""
procurement/vendor_bank_upload.py

Loading a list of suppliers and their bank accounts in one go.

CFO 2026-08-20: "Create a place where people can upload the supplier names. We
already have the list of suppliers and the bank accounts and they load it now
itself so they don't need to really do hard work." Confirmed 2026-08-21: build
the screen, and the list comes from his own file.

Why this file exists rather than more code in the view: the command-line loader
(`manage.py import_vendor_bank_accounts`) and the upload screen must apply the
SAME rules. Two copies of a banking-details validator is how one path ends up
accepting what the other refuses — the shape of the half-applied fix that has
bitten this repo before.

Three things this does that the old CSV loader did not:

  * Reads the file the way it arrives. The columns are matched by meaning, not
    by exact spelling, because nobody's supplier list has a header called
    `account_holder_name`. Excel as well as CSV.

  * Says what will happen to EVERY row, before anything is written. The old
    loader counted skips and moved on, so a row that quietly did nothing looked
    the same as a row that worked.

  * Refuses to change a supplier's bank account silently. CFO 2026-08-20, on
    the payment-request screen: "if a person is changing the bank account
    details it rejects, saying 'Why are you doing this because you paid this
    person with another bank account?'" A spreadsheet is exactly how that
    change would slip through unnoticed, so a row that moves an existing
    supplier onto a new account is HELD and named, never applied by the upload.

Nothing here makes an account payable. Every row that loads lands as a DRAFT and
still has to be approved by someone else on /vendor-banking — the maker-checker
on the model is untouched.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Reading the columns as they actually arrive
# ---------------------------------------------------------------------------
# Real supplier lists call these things whatever the person who made the sheet
# felt like. Matching on meaning costs a table; matching on exact spelling costs
# Finance an afternoon of renaming headers, which is the work this is supposed
# to remove.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    'vendor_name': (
        'vendor name', 'vendor', 'supplier name', 'supplier', 'payee',
        'payee name', 'name', 'beneficiary', 'beneficiary name', 'company',
        'company name', 'creditor', 'creditor name', 'account name',
    ),
    'account_holder_name': (
        'account holder name', 'account holder', 'holder', 'holder name',
        'name on account', 'account name as per bank', 'bank account name',
        'account title',
    ),
    'bank_name': (
        'bank name', 'bank', 'bankers', 'banker', 'financial institution',
    ),
    'account_number': (
        # Normalising strips punctuation, so 'A/C No.' arrives here as 'a c no'
        # — the spelling people actually use on a supplier list.
        'account number', 'account no', 'account', 'acc no', 'acct no',
        'acct number', 'bank account number', 'bank account', 'a c no',
        'a c number', 'ac no', 'ac number',
    ),
    'branch_code': (
        'branch code', 'branch', 'sort code', 'sortcode', 'branch no',
        'branch number', 'clearing code',
    ),
    'branch_name': ('branch name', 'branch description'),
    'swift_bic': ('swift', 'swift bic', 'bic', 'swift code', 'swift/bic'),
    'iban': ('iban', 'iban number'),
    'currency_code': ('currency code', 'currency', 'ccy', 'cur'),
    'is_default': ('is default', 'default', 'primary', 'main account'),
    'notes': ('notes', 'note', 'comment', 'comments', 'remarks', 'remark'),
}

#: Without these four a row is not a bank account.
REQUIRED_FIELDS = ('vendor_name', 'bank_name', 'account_holder_name',
                   'account_number')

#: But the SHEET only has to carry three of them. A list without a separate
#: account-holder column is saying the holder is the supplier, which is true on
#: almost every row of a real supplier list — refusing the whole file over it
#: would send Finance back to add a column that just repeats the first one.
REQUIRED_COLUMNS = ('vendor_name', 'bank_name', 'account_number')

TRUTHY = {'true', 'yes', '1', 'y', 't', 'default', 'primary'}

#: Botswana account numbers run to eleven digits; other countries differ, so
#: this is a sanity bound, not a format rule. Anything outside it is far more
#: likely a phone number or a reference pasted into the wrong column.
ACCOUNT_MIN_DIGITS = 5
ACCOUNT_MAX_DIGITS = 34          # the IBAN ceiling


def _norm_header(h) -> str:
    """'A/C No.' and 'a c no' both land on the same key."""
    s = re.sub(r'[^a-z0-9]+', ' ', str(h or '').strip().lower())
    return re.sub(r'\s+', ' ', s).strip()


def map_headers(headers) -> dict[str, str]:
    """{our field: the column it came from}, by meaning.

    An exact alias wins over a partial one, so a sheet carrying both 'Name' and
    'Account Holder Name' puts the second on `account_holder_name` rather than
    letting whichever came first take it.
    """
    seen = [(h, _norm_header(h)) for h in (headers or []) if str(h or '').strip()]
    out: dict[str, str] = {}
    taken: set[str] = set()

    for field_name, aliases in COLUMN_ALIASES.items():
        for original, norm in seen:
            if original in taken:
                continue
            if norm in aliases:
                out[field_name] = original
                taken.add(original)
                break

    # Second pass: a header that CONTAINS an alias, for things like
    # "Supplier Bank Account Number (BWP)".
    #
    # Only MULTI-WORD aliases are allowed to match this way. A single word is
    # far too eager: 'account' would take "Account Opened" as the account
    # number, and a date like 2024-01-15 strips to eight digits, which passes
    # every check and loads as a real bank account. Likewise 'bank' would take
    # "Bank Charges" and 'branch' would take "Branch Manager".
    for field_name, aliases in COLUMN_ALIASES.items():
        if field_name in out:
            continue
        multi = [a for a in aliases if ' ' in a]
        for original, norm in seen:
            if original in taken:
                continue
            if any(a in norm for a in multi):
                out[field_name] = original
                taken.add(original)
                break
    return out


def _rows_from_csv(data: bytes) -> tuple[list[str], list[dict]]:
    text = data.decode('utf-8-sig', errors='replace')
    # Sniff the separator: exports out of Excel in some locales use ';'.
    sample = text[:4096]
    delim = ','
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
    except csv.Error:
        pass
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any((c or '').strip() for c in r)]
    if not rows:
        return [], [], 0
    headers = [c.strip() for c in rows[0]]
    body = [dict(zip(headers, r + [''] * (len(headers) - len(r)))) for r in rows[1:]]
    return headers, body, 1


def _excel_cell(value) -> str:
    """A spreadsheet cell as the text a person typed into it.

    An account number keyed into Excel comes back as a float, so 10000000001
    reads as "10000000001.0" — and an account number with .0 on the end is one
    a payment would be sent to. Whole numbers are rendered without the point.
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows_from_excel(data: bytes) -> tuple[list[str], list[dict]]:
    """Excel, because that is what a supplier list actually arrives as.

    Read through python-calamine, the reader the rest of this codebase already
    uses (core/doc_parse/xlsx_md.py): one path for xlsx, xlsb, xls and ods, so a
    list saved out of an old Excel is not refused for being the wrong flavour of
    spreadsheet. The first sheet is the one used — a supplier list that keeps its
    table on sheet three is rare enough to handle by hand.
    """
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_filelike(io.BytesIO(data))
    raw = wb.get_sheet_by_name(wb.sheet_names[0]).to_python()
    rows = []
    for r in raw:
        cells = [_excel_cell(c) for c in r]
        if any(cells):
            rows.append(cells)
    if not rows:
        return [], [], 0
    # The header is the first row with at least two filled cells — a sheet
    # often opens with a title line above the real table.
    start = 0
    for i, r in enumerate(rows[:10]):
        if sum(1 for c in r if c) >= 2:
            start = i
            break
    headers = rows[start]
    body = [dict(zip(headers, r + [''] * (len(headers) - len(r))))
            for r in rows[start + 1:]]
    return [h for h in headers], body, start + 1


def read_table(data: bytes, filename: str = ''
               ) -> tuple[list[str], list[dict], int]:
    """(headers, rows, header_row) from an uploaded file.

    `header_row` is which line of their sheet the headings were on, so a
    problem can be reported against the row number the person sees in Excel
    rather than a count of data rows — after a title line those differ by two,
    which is exactly enough to send someone looking at the wrong row.
    """
    if (filename or '').lower().endswith(
            ('.xlsx', '.xlsm', '.xltx', '.xlsb', '.xls', '.ods')):
        return _rows_from_excel(data)
    # A spreadsheet named .csv by whoever exported it. Only worth trying when
    # the bytes say so: xlsx/ods are zip containers, and the old .xls format has
    # its own signature.
    if data[:2] == b'PK' or data[:4] == b'\xd0\xcf\x11\xe0':
        try:
            return _rows_from_excel(data)
        except Exception:           # noqa: BLE001 — then read it as text instead
            pass
    return _rows_from_csv(data)


# ---------------------------------------------------------------------------
# What will happen to each row
# ---------------------------------------------------------------------------
class Verdict:
    """What the screen tells the person about one line of their sheet."""

    LOAD             = 'load'              # will be created as a DRAFT
    NEW_SUPPLIER     = 'load_new_supplier'  # ditto, and the supplier is new
    ALREADY_ON_FILE  = 'already'            # same supplier, same account
    BANK_CHANGED     = 'bank_changed'       # HELD — a different account
    MISSING          = 'missing'            # a required column is empty
    BAD_ACCOUNT      = 'bad_account'        # not plausibly an account number
    BAD_CURRENCY     = 'bad_currency'

    #: The two that write something.
    WRITES = (LOAD, NEW_SUPPLIER)

    LABELS = {
        LOAD:            'Will be added, waiting for approval',
        NEW_SUPPLIER:    'New supplier — will be added, waiting for approval',
        ALREADY_ON_FILE: 'Already on file — nothing to do',
        BANK_CHANGED:    'HELD — this moves the supplier to a different account',
        MISSING:         'Missing something we must have',
        BAD_ACCOUNT:     'That does not look like an account number',
        BAD_CURRENCY:    'We do not hold that currency',
    }


@dataclass
class RowPlan:
    line: int                       # the row number in their sheet, 1-based
    verdict: str
    reason: str = ''
    vendor_name: str = ''
    bank_name: str = ''
    account_holder_name: str = ''
    account_number: str = ''
    branch_code: str = ''
    branch_name: str = ''
    swift_bic: str = ''
    iban: str = ''
    currency_code: str = 'BWP'
    is_default: bool = True
    notes: str = ''
    existing_account_last4: str = ''    # only for a held bank change
    contact_id: str = ''

    @property
    def will_write(self) -> bool:
        return self.verdict in Verdict.WRITES

    def public(self) -> dict:
        """What the screen may show. Account numbers are cut to their last four
        digits — a full list of supplier account numbers on screen is the thing
        an attacker most wants, and the person uploading already has the file.
        """
        return {
            'line': self.line,
            'verdict': self.verdict,
            'label': Verdict.LABELS.get(self.verdict, self.verdict),
            'reason': self.reason,
            'supplier': self.vendor_name,
            'bank': self.bank_name,
            'account_holder': self.account_holder_name,
            'account_ends': self.account_number[-4:] if self.account_number else '',
            'branch_code': self.branch_code,
            'currency': self.currency_code,
            'existing_account_ends': self.existing_account_last4,
            'will_load': self.will_write,
        }


@dataclass
class UploadPlan:
    rows: list[RowPlan] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    mapped: dict[str, str] = field(default_factory=dict)
    unmapped_columns: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        c: dict[str, int] = {}
        for r in self.rows:
            c[r.verdict] = c.get(r.verdict, 0) + 1
        return c

    def public(self) -> dict:
        counts = self.counts()
        return {
            'total_rows': len(self.rows),
            'will_load': sum(1 for r in self.rows if r.will_write),
            'held': counts.get(Verdict.BANK_CHANGED, 0),
            'already_on_file': counts.get(Verdict.ALREADY_ON_FILE, 0),
            'rejected': sum(counts.get(v, 0) for v in
                            (Verdict.MISSING, Verdict.BAD_ACCOUNT,
                             Verdict.BAD_CURRENCY)),
            'counts': counts,
            'columns_understood': self.mapped,
            'columns_ignored': self.unmapped_columns,
            'missing_columns': [f for f in REQUIRED_COLUMNS
                                if f not in self.mapped],
            'rows': [r.public() for r in self.rows],
        }


def _digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')


def _plausible_account(number: str) -> bool:
    d = _digits(number)
    if ACCOUNT_MIN_DIGITS <= len(d) <= ACCOUNT_MAX_DIGITS:
        return True
    # An IBAN is letters then digits, so judge it on its whole length.
    stripped = re.sub(r'[\s-]', '', number or '')
    return bool(re.fullmatch(r'[A-Za-z]{2}[0-9A-Za-z]{13,32}', stripped))


def normalise_name(name: str) -> str:
    """Match supplier names the way a person would: case, spacing and the
    incorporation suffixes nobody types consistently are all noise.

    Only the suffixes that say HOW a company is incorporated are stripped.
    'Holdings', 'Group' and 'Company' are NOT noise — they are how two separate
    legal entities in the same family tell each other apart, and collapsing
    them would attach a bank account to the wrong company. Against 9,403
    supplier names that is not a theoretical risk.
    """
    s = (name or '').strip().lower()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\b(pty|proprietary|ltd|limited|inc|incorporated|cc|the)\b',
               ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def build_plan(data: bytes, filename: str = '', company_id=None) -> UploadPlan:
    """Read the file and decide what happens to every row. Writes nothing.

    Every lookup here is against what is already on file, so the person sees
    'already on file' and 'this changes the bank account' BEFORE they commit,
    which is the whole point of a preview.

    `company_id` is the company the list is being loaded for. Supplier records
    belong to a company — CFO 2026-05-18: ADIC's vendors must not appear when
    ADSA is selected — so the 'do we already know this supplier' question has
    to be asked within that company, or a list loaded for one entity would
    match against another's suppliers.
    """
    from billing.models import Contact
    from core.models import Currency
    from procurement.models import VendorBankAccount

    headers, raw_rows, header_row = read_table(data, filename)
    mapped = map_headers(headers)
    plan = UploadPlan(
        headers=list(headers), mapped=mapped,
        unmapped_columns=[h for h in headers if h not in set(mapped.values())],
    )
    if not raw_rows:
        return plan

    currencies = set(Currency.objects.values_list('code', flat=True))

    # One pass over the vendors we already know, keyed the forgiving way, so a
    # 5,000-row sheet does not become 5,000 name queries. Scoped to the company
    # being loaded for, plus the older rows that predate company stamping.
    from django.db.models import Q
    vendors = Contact.objects.filter(contact_type=Contact.ContactType.VENDOR)
    if company_id:
        vendors = vendors.filter(Q(company_id=company_id) |
                                 Q(company_id__isnull=True))
    known: dict[str, object] = {}
    for c in vendors.only('id', 'name', 'company_id').iterator():
        # A company's own record wins over a legacy unstamped one with the
        # same name, so a match never silently prefers the older row.
        key = normalise_name(c.name)
        if key not in known or (company_id and str(
                getattr(c, 'company_id', '') or '') == str(company_id)):
            known[key] = c

    def cell(row, field_name):
        col = mapped.get(field_name)
        return (str(row.get(col, '') or '').strip() if col else '')

    for i, row in enumerate(raw_rows, start=1):
        # The row number as it appears in their file.
        p = RowPlan(line=header_row + i, verdict=Verdict.LOAD)
        p.vendor_name         = cell(row, 'vendor_name')
        p.bank_name           = cell(row, 'bank_name')
        p.account_number      = cell(row, 'account_number')
        p.branch_code         = cell(row, 'branch_code')
        p.branch_name         = cell(row, 'branch_name')
        p.swift_bic           = cell(row, 'swift_bic')
        p.iban                = cell(row, 'iban')
        p.notes               = cell(row, 'notes')
        # A sheet that names the account holder separately is telling us
        # something; one that does not is telling us it is the supplier.
        p.account_holder_name = cell(row, 'account_holder_name') or p.vendor_name
        ccy = (cell(row, 'currency_code') or 'BWP').upper()
        p.currency_code = ccy
        dflt = cell(row, 'is_default')
        p.is_default = (dflt.lower() in TRUTHY) if dflt else True

        blank = [f.replace('_', ' ') for f in REQUIRED_FIELDS
                 if not getattr(p, f, '')]
        if blank:
            p.verdict = Verdict.MISSING
            p.reason = 'no ' + ', no '.join(blank)
            plan.rows.append(p)
            continue

        if not _plausible_account(p.account_number):
            p.verdict = Verdict.BAD_ACCOUNT
            p.reason = (f'{len(_digits(p.account_number))} digits — an account '
                        f'number should have between {ACCOUNT_MIN_DIGITS} and '
                        f'{ACCOUNT_MAX_DIGITS}')
            plan.rows.append(p)
            continue

        if ccy not in currencies:
            p.verdict = Verdict.BAD_CURRENCY
            p.reason = f'{ccy} is not one of the currencies we hold'
            plan.rows.append(p)
            continue

        contact = known.get(normalise_name(p.vendor_name))
        if contact is None:
            p.verdict = Verdict.NEW_SUPPLIER
            p.reason = 'not on file yet, so the supplier will be created too'
            plan.rows.append(p)
            continue

        p.contact_id = str(contact.pk)
        live = list(VendorBankAccount.objects
                    .filter(contact=contact)
                    .exclude(status=VendorBankAccount.Status.RETIRED)
                    .exclude(status=VendorBankAccount.Status.REJECTED)
                    .values_list('account_number', flat=True))
        wanted = _digits(p.account_number)
        if any(_digits(a) == wanted for a in live):
            p.verdict = Verdict.ALREADY_ON_FILE
            p.reason = 'we already hold this account for them'
        elif live:
            # The CFO's rule, applied to a spreadsheet: a sheet must never be
            # the thing that moves a supplier's money to a new account.
            p.verdict = Verdict.BANK_CHANGED
            p.existing_account_last4 = (_digits(live[0]) or '')[-4:]
            p.reason = ('we already pay them on another account ending '
                        f'{p.existing_account_last4}. Somebody has to say why '
                        'this changed, so this row will not be loaded.')
        plan.rows.append(p)

    return plan


def _no_account_numbers(text: str) -> str:
    """A database error can quote the row it choked on, account number and all.

    The preview masks account numbers on purpose; a failure message must not be
    the hole that undoes it. Any run of five or more digits is cut to its last
    four, which is all anyone needs to find the row in their own sheet.
    """
    return re.sub(r'\d{5,}', lambda m: '…' + m.group(0)[-4:], str(text))


def apply_plan(plan: UploadPlan, user, company_id=None) -> dict:
    """Create the rows the plan said would load. Nothing else.

    Every account lands DRAFT: the approval on /vendor-banking is the control,
    and an upload must not be a way around it.

    `company_id` is required to create a supplier that was not already on file.
    A Contact with no company is invisible in every company-filtered picker, so
    loading thousands of them would look like success and leave a register
    nobody can select from.
    """
    from django.db import transaction
    from billing.models import Contact
    from procurement.models import VendorBankAccount

    created = 0
    suppliers_created = 0
    problems: list[str] = []

    for p in plan.rows:
        if not p.will_write:
            continue
        try:
            with transaction.atomic():
                if p.contact_id:
                    contact = Contact.objects.get(pk=p.contact_id)
                else:
                    if not company_id:
                        raise ValueError(
                            'no company was chosen, so a new supplier cannot '
                            'be created — pick the company at the top of the '
                            'screen and load the list again')
                    contact, made = Contact.objects.get_or_create(
                        contact_type=Contact.ContactType.VENDOR,
                        name=p.vendor_name[:200],
                        company_id=company_id,
                    )
                    suppliers_created += 1 if made else 0
                VendorBankAccount.objects.create(
                    contact=contact,
                    bank_name=p.bank_name[:200],
                    account_holder_name=p.account_holder_name[:300],
                    account_number=p.account_number[:40],
                    branch_code=p.branch_code[:20],
                    branch_name=p.branch_name[:200],
                    swift_bic=p.swift_bic[:12],
                    iban=p.iban[:40],
                    currency_code_id=p.currency_code,
                    is_default=p.is_default,
                    notes=(f'Loaded from a supplier list on '
                           f'{p.line and "row " + str(p.line)}. '
                           f'{p.notes}').strip()[:2000],
                    status=VendorBankAccount.Status.DRAFT,
                    created_by=user,
                )
                created += 1
        except Exception as exc:                            # noqa: BLE001
            # One bad row must not lose the other four thousand, and the reason
            # has to reach the person rather than a log nobody opens.
            problems.append(_no_account_numbers(
                f'row {p.line} ({p.vendor_name}): {exc}'))

    return {
        'created': created,
        'suppliers_created': suppliers_created,
        'held': sum(1 for r in plan.rows if r.verdict == Verdict.BANK_CHANGED),
        'already_on_file': sum(1 for r in plan.rows
                               if r.verdict == Verdict.ALREADY_ON_FILE),
        'not_loaded': sum(1 for r in plan.rows if not r.will_write),
        'problems': problems,
    }
