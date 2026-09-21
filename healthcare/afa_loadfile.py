"""
healthcare/afa_loadfile.py — Phase 2 of the ADH → AFA load file.

Turns the Graphite membership (healthcare/afa_members.py) into AFA's
pipe-delimited 35-column file, layout ADH_000015.

The rules below are AFA's, taken from their own layout sheet, and are enforced
here rather than trusted to whoever fills in a spreadsheet:

  * registration date is ALWAYS the 1st of a month
  * resignation date is ALWAYS the end of a month
  * dependant 0 is the principal; dependants carry the principal's ADH number
  * suspension defaults to N
  * group name must match iMed EXACTLY
  * resignation reason must be one of AFA's ten codes
  * plan must be one of AFA's five options
  * the file is PIPE delimited, never comma

HOLD, NEVER GUESS. Anything unresolvable — a group with no iMed mapping, a plan
that isn't on AFA's list, a cancellation reason we can't map — is held back and
reported, not defaulted. A held row is a day's delay; a guessed row is a member
walking into a hospital with the wrong cover.

DATA PROTECTION (AD-POL-AI-GOV-001): the rendered rows are policyholder data.
They go into the file and to AFA, who administer the scheme. They are never
logged, never returned in an error message, and never sent to any external
model. Every log line here is a count.
"""
from __future__ import annotations

import calendar
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger('afa-loadfile')

DELIMITER = '|'

# AFA layout ADH_000015 — order is the contract, do not reorder.
COLUMNS: Tuple[str, ...] = (
    'ADH Policy Number', 'Group Name', 'Region Name', 'Registration Date',
    'Dependant no', 'Dependant Type', 'Relationship', 'Employee Number',
    'Title', 'First Name', 'Surname', 'Date of Birth', 'IDNumber', 'Passport',
    'Gender', 'Resignation date', 'Resignation reason', 'Suspension',
    'Suspension date', 'CellNumber', 'EmailAddress', 'Address line 1',
    'Address Line 2', 'Address Line 3', 'Town', 'Postal Code', 'Option',
    'Conditions', 'Waiting period Eff From', 'Waiting period Eff To',
    'Account holder name', 'Bank Name', 'Branch Code', 'Account Number',
    'Account Type',
)

# AFA's closed list of resignation reasons.
AFA_REASONS = (
    'RESIGNED', 'NON PAYMENT', 'DECEASED', 'RESIGNATION OF BENEFICIARY',
    'AFFORDABILITY', 'DISSATISFIED CLIENT', 'DISSATISFIED WITH SERVICE',
    'DISSATISFIED WITH PRODUCT', 'POLICY NOT TAKEN UP', 'NOT AVAILABLE',
)

# AFA's closed list of plans ("Option").
AFA_OPTIONS = ('AD Lite', 'AD Essential', 'AD Core', 'AD Premier', 'AD Status')

DEP_TYPE_PRINCIPAL = 'Principle Member'   # AFA's spelling, not ours.
DEP_TYPE_SPOUSE    = 'Spouse'
DEP_TYPE_DEPENDANT = 'Dependant'

REL_MAIN  = 'Main Member'
REL_CHILD = 'Child'
REL_DEP   = 'Dependant'

# Safety fence (checklist H25): if a pull loses more than this share of the
# members we already know about, the run aborts rather than reporting them all
# as departures.
MAX_SHRINK_RATIO = 0.10
MAX_REPLICA_LAG_SECONDS = 900


class LoadFileAborted(Exception):
    """The safety fence stopped the run. Nothing is written or sent."""


@dataclass
class HeldRow:
    policy_number: str
    dependant_no: int
    reason: str


@dataclass
class BuildResult:
    body: str
    rows: int
    new: int = 0
    changed: int = 0
    departures: int = 0
    held: List[HeldRow] = field(default_factory=list)
    source_rows: int = 0
    replica_lag: Optional[int] = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body.encode('utf-8')).hexdigest()

    def held_summary(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for h in self.held:
            out[h.reason] = out.get(h.reason, 0) + 1
        return out


# --------------------------------------------------------------------------
# AFA date rules
# --------------------------------------------------------------------------

def _as_date(value: Any) -> Optional[date]:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(str(value).strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def first_of_month(value: Any) -> str:
    """AFA: the registration date is ALWAYS the 1st of a month."""
    d = _as_date(value)
    return d.replace(day=1).isoformat() if d else ''


def end_of_month(value: Any) -> str:
    """AFA: the resignation date must ALWAYS be the end of the month."""
    d = _as_date(value)
    if not d:
        return ''
    return d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()


def iso_date(value: Any) -> str:
    d = _as_date(value)
    return d.isoformat() if d else ''


def normalise_gender(value: Any) -> str:
    """Graphite stores gender as an integer, AFA want the word.

    1 = Male, 0 = Female. Confirmed in eight independent places in the Graphite
    source — PolicyResource, QuotesExport, QuoteController, AdGroupKycAdmin, the
    quote-edit select options — and stated outright in
    `frontend/src/api/reratePremium.ts`: "0 female / 1 male". Both the principal
    (`customer_profile.gender`) and the dependant
    (`ad_grouped_policy_beneficiary.gender`) use the same codes.

    Text forms are accepted too, because imports have written 'Male'/'M' in the
    past. Anything else returns blank, which HOLDS the row — inventing a gender
    is worse than a day's delay.
    """
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return 'MALE' if value else 'FEMALE'
    if isinstance(value, int):
        return {1: 'MALE', 0: 'FEMALE'}.get(value, '')
    v = str(value).strip().upper()
    if v in ('1', '0'):
        return 'MALE' if v == '1' else 'FEMALE'
    if v.startswith('M'):
        return 'MALE'
    if v.startswith('F'):
        return 'FEMALE'
    return ''


def dependant_labels(person_type: Any, relation: Any) -> Tuple[str, str]:
    """Map Graphite's free-ish dependant wording onto AFA's two closed columns.

    Live data carries 'Dependent'/'Dependant'/'Employee' against relations like
    'Child Dependent', 'Adult Dependent' and 'Spouse' — inconsistent enough that
    the RELATION is the reliable signal, not person_type.
    """
    rel = (str(relation or '')).strip().lower()
    if 'spouse' in rel:
        return DEP_TYPE_SPOUSE, REL_DEP
    if 'child' in rel:
        return DEP_TYPE_DEPENDANT, REL_CHILD
    if 'adult' in rel or 'depend' in rel:
        return DEP_TYPE_DEPENDANT, REL_DEP
    return '', ''


def _clean(value: Any) -> str:
    """Strip the delimiter out of a field so one address cannot shift the row."""
    text = '' if value is None else str(value)
    return text.replace(DELIMITER, ' ').replace('\r', ' ').replace('\n', ' ').strip()


# --------------------------------------------------------------------------
# Row rendering
# --------------------------------------------------------------------------

def render_row(values: Dict[str, Any]) -> str:
    if len(COLUMNS) != 35:                       # guards a careless edit above
        raise AssertionError(f'AFA layout must be 35 columns, got {len(COLUMNS)}')
    return DELIMITER.join(_clean(values.get(col, '')) for col in COLUMNS)


def row_hash(rendered: str) -> str:
    return hashlib.sha256(rendered.encode('utf-8')).hexdigest()


def build_principal_row(
    member: Dict[str, Any],
    *,
    imed_group_name: str,
    region_name: str,
    resignation: Optional[Tuple[str, str]] = None,
    suspension: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    """The 35 fields for a principal member (dependant number 0)."""
    resign_date, resign_reason = resignation or ('', '')
    susp_flag, susp_date = suspension or ('N', '')
    return {
        'ADH Policy Number': member.get('policy_number'),
        'Group Name':        imed_group_name,
        'Region Name':       region_name,
        'Registration Date': first_of_month(
                                 member.get('policy_start_date') or member.get('activated_date')),
        'Dependant no':      0,
        'Dependant Type':    DEP_TYPE_PRINCIPAL,
        'Relationship':      REL_MAIN,
        'Employee Number':   member.get('employee_number'),
        # Graphite has no title field on customer or customer_profile — left
        # blank deliberately rather than inventing one. Raised with Ritah.
        'Title':             '',
        'First Name':        member.get('first_name'),
        'Surname':           member.get('surname'),
        'Date of Birth':     iso_date(member.get('dob')),
        'IDNumber':          member.get('id_number'),
        'Passport':          member.get('passport'),
        'Gender':            normalise_gender(member.get('gender')),
        'Resignation date':  resign_date,
        'Resignation reason': resign_reason,
        'Suspension':        susp_flag,
        'Suspension date':   susp_date,
        'CellNumber':        member.get('cellphone'),
        'EmailAddress':      member.get('email'),
        'Address line 1':    member.get('address_1'),
        'Address Line 2':    member.get('address_2'),
        'Address Line 3':    member.get('address_3'),
        'Town':              member.get('town'),
        'Postal Code':       '',
        'Option':            member.get('plan_name'),
        'Conditions':        '',
        'Waiting period Eff From': iso_date(member.get('waiting_from')),
        'Waiting period Eff To':   iso_date(member.get('waiting_to')),
        'Account holder name': member.get('account_holder'),
        'Bank Name':         member.get('bank_name'),
        'Branch Code':       member.get('branch_code'),
        'Account Number':    member.get('account_number'),
        'Account Type':      member.get('account_type'),
    }


def build_dependant_row(
    dep: Dict[str, Any],
    principal: Dict[str, Any],
    dependant_no: int,
    *,
    imed_group_name: str,
    region_name: str,
    resignation: Optional[Tuple[str, str]] = None,
) -> Dict[str, Any]:
    """The 35 fields for a dependant.

    The ADH policy number, group, region, registration date and plan are the
    PRINCIPAL's — AFA reject a dependant carrying its own policy number. Banking
    is deliberately left blank: the principal pays.
    """
    dep_type, relationship = dependant_labels(dep.get('person_type'), dep.get('relation'))
    resign_date, resign_reason = resignation or ('', '')
    return {
        'ADH Policy Number': principal.get('policy_number'),
        'Group Name':        imed_group_name,
        'Region Name':       region_name,
        'Registration Date': first_of_month(
                                 principal.get('policy_start_date') or principal.get('activated_date')),
        'Dependant no':      dependant_no,
        'Dependant Type':    dep_type,
        'Relationship':      relationship,
        'Employee Number':   principal.get('employee_number'),
        'Title':             '',
        'First Name':        dep.get('first_name'),
        'Surname':           dep.get('surname'),
        'Date of Birth':     iso_date(dep.get('dob')),
        'IDNumber':          dep.get('id_number'),
        'Passport':          dep.get('passport'),
        'Gender':            normalise_gender(dep.get('gender')),
        'Resignation date':  resign_date,
        'Resignation reason': resign_reason,
        'Suspension':        'N',
        'Suspension date':   '',
        'CellNumber':        dep.get('cellphone'),
        'EmailAddress':      dep.get('email'),
        'Address line 1':    '',
        'Address Line 2':    dep.get('address_2'),
        'Address Line 3':    '',
        'Town':              dep.get('town'),
        'Postal Code':       '',
        'Option':            principal.get('plan_name'),
        'Conditions':        '',
        'Waiting period Eff From': '',
        'Waiting period Eff To':   '',
        'Account holder name': '',
        'Bank Name':         '',
        'Branch Code':       '',
        'Account Number':    '',
        'Account Type':      '',
    }


# --------------------------------------------------------------------------
# Validation — hold, never guess
# --------------------------------------------------------------------------

def validate_row(values: Dict[str, Any]) -> Optional[str]:
    """Return a plain-English reason to HOLD this row, or None to send it."""
    if not _clean(values.get('ADH Policy Number')):
        return 'no ADH policy number'
    if not _clean(values.get('Group Name')):
        return 'group name not mapped to iMed'
    option = _clean(values.get('Option'))
    if option not in AFA_OPTIONS:
        return f'plan "{option or "(blank)"}" is not one of AFA\'s five options'
    reg = _clean(values.get('Registration Date'))
    if not reg:
        return 'no registration date'
    if not reg.endswith('-01'):
        return 'registration date is not the 1st of a month'
    reason = _clean(values.get('Resignation reason'))
    if reason and reason not in AFA_REASONS:
        return f'resignation reason "{reason}" is not on AFA\'s list'
    resign = _clean(values.get('Resignation date'))
    if reason and not resign:
        return 'resignation reason given with no resignation date'
    if resign:
        d = _as_date(resign)
        if d and d.day != calendar.monthrange(d.year, d.month)[1]:
            return 'resignation date is not the end of a month'
    if _clean(values.get('Suspension')) not in ('Y', 'N'):
        return 'suspension flag is not Y or N'
    if not _clean(values.get('First Name')) or not _clean(values.get('Surname')):
        return 'missing name'
    if not _clean(values.get('Date of Birth')):
        return 'missing date of birth'
    if not _clean(values.get('IDNumber')) and not _clean(values.get('Passport')):
        return 'no ID or passport number'
    if not _clean(values.get('Gender')):
        return 'gender missing or unreadable'
    dep_no = values.get('Dependant no')
    if dep_no != 0 and not _clean(values.get('Dependant Type')):
        return 'dependant relationship could not be mapped'
    return None


# --------------------------------------------------------------------------
# The safety fence
# --------------------------------------------------------------------------

def check_fence(
    *,
    source_rows: int,
    known_members: int,
    columns_seen: Iterable[str],
    replica_lag: Optional[int],
    required_columns: Iterable[str] = ('policy_number', 'employer_group_id', 'plan_name'),
) -> None:
    """Abort the run rather than emit nonsense (checklist H25).

    A replica outage, broken replication or a renamed upstream column makes the
    pull come back empty or short. Diffed against the snapshot that reads as
    every member having resigned — a file that cancels the entire scheme, built
    by a pipeline reporting itself perfectly healthy.
    """
    missing = [c for c in required_columns if c not in set(columns_seen)]
    if missing:
        raise LoadFileAborted(
            f'the membership query came back without these columns: {", ".join(missing)}. '
            'The Graphite schema has probably changed. No file was built.'
        )
    if source_rows == 0:
        raise LoadFileAborted(
            'the membership query returned no members at all. Treating that as '
            'everyone resigning would cancel the whole scheme, so nothing was built.'
        )
    if known_members and source_rows < known_members * (1 - MAX_SHRINK_RATIO):
        lost = known_members - source_rows
        raise LoadFileAborted(
            f'the membership dropped from {known_members} to {source_rows} '
            f'({lost} members) in one run. That is more than a normal day, so '
            'nothing was built — check the replica before releasing anything.'
        )
    if replica_lag is not None and replica_lag > MAX_REPLICA_LAG_SECONDS:
        raise LoadFileAborted(
            f'the Graphite replica is {replica_lag} seconds behind. Its membership '
            'is stale, so the file was not built.'
        )


def render_file(rows: List[str], *, include_header: bool = True) -> str:
    """Join rendered rows into the pipe-delimited body AFA ingests."""
    out: List[str] = []
    if include_header:
        out.append(DELIMITER.join(COLUMNS))
    out.extend(rows)
    return '\n'.join(out) + '\n'
