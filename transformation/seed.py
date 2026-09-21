"""The four-month path to "First AI Insurance Company in Botswana".

The blueprint of 2026-09-20 laid this out over 18 months. The CFO cut it to
four: everything ships by 20 January 2027. That is only possible because most
of it is already built and switched off — Month 1 is almost entirely arming
things, not writing them.

Managers are seeded ONLY where the notebook states the role outright. Where it
does not, the row says "TBC — CFO to name" rather than guessing: this board
puts names next to an automation score, and a guessed name is a smear.

Re-running the seed is safe. It updates the wording, the money and the dates,
and never touches percent or status — those belong to whoever is doing the work.
"""
from __future__ import annotations

import datetime as _dt

from transformation.models import DepartmentPlan, Initiative

TBC = 'TBC — CFO to name'

# What a re-seed is allowed to overwrite on a row that already exists.
# Deliberately excludes manager_name / manager_email / blocked_on / status /
# percent — those belong to whoever is doing the work, not to this file.
REFRESHABLE_FIELDS = (
    'title', 'plain_summary', 'track', 'month', 'target_date',
    'annual_saving_bwp', 'fte_released', 'is_vendor',
    'improves_customer_service', 'devlog_area',
)

# Which Build Log areas serve which department. Drives the adoption score:
# "we shipped this for you — did anyone confirm using it?"
DEPARTMENT_AREAS: dict[str, list[str]] = {
    'Finance & Planning': ['finance', 'payments', 'banking', 'fnb', 'ledger',
                           'billing', 'realpay', 'reconciliation', 'payroll'],
    'Claims': ['claims', 'claims_automation', 'salvage'],
    'Underwriting': ['underwriting', 'policy', 'quotes'],
    'Software Development': ['omni', 'devlog', 'ci'],
    'Admin & IT': ['admin', 'it', 'licensing', 'access'],
    'Human Capital': ['hris', 'hr', 'recruitment'],
    'Compliance': ['compliance', 'regulatory', 'nbfira', 'kyc', 'iso_compliance'],
    'Business Development': ['broker', 'commissions'],
    'Sales & Marketing': ['leads', 'sales', 'marketing'],
    'Health Insurance': ['healthcare'],
    'UniCoin': ['unicoin', 'agent_portal'],
    'Veritas': ['veritas', 'salvage', 'parts'],
    'Executive': ['boardroom', 'reporting'],
    'Special Projects': ['nexus', 'aware'],
    'Operations': ['operations', 'taskboard'],
}

M = {1: '2026-10-20', 2: '2026-11-20', 3: '2026-12-20', 4: '2027-01-20'}


def _i(code, title, summary, track, month, dept, manager, email,
       saving=0, fte=0.0, vendor=False, service=False, area='', blocked_on=''):
    return dict(
        code=code, title=title, plain_summary=summary, track=track, month=month,
        department=dept, manager_name=manager, manager_email=email,
        target_date=_dt.date.fromisoformat(M[month]),
        annual_saving_bwp=saving, fte_released=fte, is_vendor=vendor,
        improves_customer_service=service, devlog_area=area, blocked_on=blocked_on,
    )


# --------------------------------------------------------------------------
# The path. Month 1 is switch-on. Months 2-4 are the two-way bridge.
# --------------------------------------------------------------------------
INITIATIVES = [
    # ---- Month 1 — switch on what is already built --------------------
    _i('SW-01', 'Turn on the refund bridge',
       'Refunds already travel from Graphite to Omni and back — the switch is off. '
       'Turning it on ends the re-typing of every refund.',
       'switch_on', 1, 'Finance & Planning', 'Keetile Mokhendo', 'kmokhendo@alphadirect.co.bw',
       saving=180000, fte=1.0, area='finance'),
    _i('SW-02', 'Land the claims event bridge on Graphite',
       'Omni is live and listening but Graphite is not yet sending. One merge and deploy '
       'starts the whole claims chain.',
       'switch_on', 1, 'Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw',
       saving=420000, fte=2.5, vendor=True, service=True, area='claims',
       blocked_on='Pramod Bisen — review and deploy Graphite PR #2181'),
    _i('SW-03', 'Sign off the claim letter wording',
       'Agreement of Loss and decline letters are written and waiting. Until the wording is '
       'signed, every letter is posted by hand.',
       'switch_on', 1, 'Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw',
       saving=90000, fte=0.6, service=True, area='claims'),
    _i('SW-04', 'Switch the renewal and re-rating jobs back on',
       'About 25 scheduled jobs in Graphite sit commented out, including policy renewals and '
       're-rating. Confirm which are dead and restore the rest.',
       'switch_on', 1, 'Underwriting', TBC, '',
       saving=240000, fte=1.5, vendor=True, area='underwriting'),
    _i('SW-05', 'Get the MotoLink parts list flowing',
       'Assessments arrive without the itemised parts, so a clerk retypes them. The vendor '
       'must add the lines; Omni already accepts them.',
       'switch_on', 1, 'Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw',
       saving=150000, fte=1.0, vendor=True, area='claims',
       blocked_on='Phil / MotoLink — itemised push'),
    _i('SW-06', 'Turn on the pre-filled claim forms',
       'The claim form library is built and off. Switching it on sends the customer a form '
       'already filled in with what we know.',
       'switch_on', 1, 'Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw',
       saving=60000, fte=0.4, service=True, area='claims'),
    _i('SW-07', 'Close the three reconciliation gaps',
       'P30.44m claims mirror, P233k RealPay receipts and the duplicated bank statement lines '
       '— each one is somebody checking by hand every month.',
       'switch_on', 1, 'Finance & Planning', 'Pako Kago', 'pkago@alphadirect.co.bw',
       saving=120000, fte=0.8, area='finance'),
    _i('SW-08', 'One list of every scheduled job',
       'There are three schedulers across the two systems. Nobody can answer "is this running?" '
       'today. One inventory, one screen, one alert when a job goes quiet.',
       'switch_on', 1, 'Software Development', 'Shingidzano Lesetedi', 'slesetedi@theriskco.com',
       saving=60000, fte=0.4, area='omni'),

    # ---- Month 2 — collections and the money loop ---------------------
    _i('CO-01', 'Failed debits chase themselves',
       'When a debit fails, Graphite tells Omni, Omni decides what to do and the customer gets '
       'the message — without a clerk opening a spreadsheet.',
       'collections', 2, 'Finance & Planning', 'Keetile Mokhendo', 'kmokhendo@alphadirect.co.bw',
       saving=360000, fte=2.0, service=True, area='realpay'),
    _i('CO-02', 'A call list that ranks itself',
       'The people chasing debtors get a list ordered by money at risk, not by whoever shouts '
       'loudest, and the outcome is written once.',
       'collections', 2, 'Finance & Planning', 'Keetile Mokhendo', 'kmokhendo@alphadirect.co.bw',
       saving=300000, fte=2.0, service=True, area='realpay'),
    _i('CO-03', 'Commission runs release themselves',
       'Finance approves the run once in Omni and the statements go out and the state lands '
       'back in Graphite.',
       'collections', 2, 'Business Development', TBC, '',
       saving=180000, fte=1.0, area='commissions'),
    _i('CO-04', 'Stop debiting cancelled policies',
       'Over eleven weeks P1.04m was taken on cancelled and lapsed policies. A rule stops it '
       'and flags what to refund.',
       'collections', 2, 'Finance & Planning', 'Pako Kago', 'pkago@alphadirect.co.bw',
       saving=240000, fte=0.5, service=True, area='realpay'),

    # ---- Month 3 — the policy lifecycle bus ---------------------------
    _i('PO-01', 'Policy changes tell Finance by themselves',
       'Cancellation, endorsement, reinstatement and renewal all currently travel by email. '
       'They become events, and the invoice or credit note raises itself.',
       'policy', 3, 'Underwriting', TBC, '',
       saving=480000, fte=3.0, vendor=True, area='underwriting'),
    _i('PO-02', 'Omni writes decisions back into Graphite',
       'The one change that removes the most work: when Omni decides, Graphite is told — '
       'nobody retypes it. States only, never money.',
       'policy', 3, 'Software Development', 'Shingidzano Lesetedi', 'slesetedi@theriskco.com',
       saving=600000, fte=4.0, vendor=True, area='omni'),
    _i('PO-03', 'Quotes that need a person get referred automatically',
       'Standard risks price and issue themselves; only the unusual ones reach an underwriter, '
       'with the claims history already attached.',
       'policy', 3, 'Underwriting', 'Gomolemo Sebudula', 'gsebudula@alphadirect.co.bw',
       saving=360000, fte=2.0, service=True, area='underwriting'),
    _i('PO-04', 'Recoveries and salvage get a home',
       'Both are tracked by hand in Graphite today. They move to the claims engine that already '
       'drafts the letters and the purchase orders.',
       'policy', 3, 'Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw',
       saving=240000, fte=1.5, area='claims'),

    # ---- Month 4 — finance close, service, acquisition ----------------
    _i('FI-01', 'Month close in two days',
       'Bank matching by rule first, the local AI only for the awkward narrations, and the '
       'checks run themselves overnight.',
       'finance', 4, 'Finance & Planning', 'Kago Tshutlhedi', 'ktshutlhedi@alphadirect.co.bw',
       saving=420000, fte=2.5, area='finance'),
    _i('FI-02', 'NBFIRA returns build themselves',
       'Every NBFIRA return is assembled from the live figures and arrives ready to review. '
       'The workbook method and the filed quarters stay exactly as signed — nothing is '
       'recalculated behind the regulator\'s back, and a person always presses file.',
       'finance', 4, 'Compliance', 'Kakale Botana', 'kbotana@alphadirect.co.bw',
       saving=180000, fte=1.0, area='nbfira'),
    _i('FI-03', 'Consolidated group accounts at the press of a button',
       'Six companies, one consolidated pack: each entity\'s trial balance collected, '
       'translated and rolled up on the frozen format. Today this is a manual workbook '
       'every month.',
       'finance', 3, 'Finance & Planning', 'Kago Tshutlhedi', 'ktshutlhedi@alphadirect.co.bw',
       saving=360000, fte=2.0, area='finance'),
    _i('FI-04', 'Related-party transactions find themselves',
       'Omni already knows which entities are ours. Any transaction between two group '
       'companies is tagged the moment it is booked, and the register builds itself for '
       'the auditors instead of being reconstructed at year end.',
       'finance', 3, 'Finance & Planning', 'Pako Kago', 'pkago@alphadirect.co.bw',
       saving=240000, fte=1.5, area='finance'),
    _i('FI-05', 'Inter-company balances eliminate on consolidation',
       'Matched pairs are cancelled automatically on the consolidation, and anything that '
       'does NOT match is listed as a difference for a person to settle — never silently '
       'squared off.',
       'finance', 3, 'Finance & Planning', 'Pako Kago', 'pkago@alphadirect.co.bw',
       saving=300000, fte=1.5, area='finance'),
    _i('FI-06', 'VAT, PAYE and company tax compute themselves',
       'The VAT return, the PAYE schedule and the tax computation are built from the '
       'ledger and the payroll run, with VAT rounded half up as the law requires. '
       'A person checks and files — Omni never submits on its own.',
       'finance', 4, 'Finance & Planning', 'Kago Tshutlhedi', 'ktshutlhedi@alphadirect.co.bw',
       saving=300000, fte=1.5, area='finance'),
    _i('FI-07', 'BURS submissions prepared and tracked',
       'Every return arrives as a ready file with the deadline on the board, who is filing '
       'it and proof of what was filed. The press of the submit button stays with a person.',
       'finance', 4, 'Compliance', 'Kakale Botana', 'kbotana@alphadirect.co.bw',
       saving=120000, fte=0.7, area='regulatory'),
    _i('FI-08', 'Management accounts prepare themselves',
       'The monthly management accounts build on their own — on the frozen format, with the '
       'same revenue mapping, no exceptions. The CFO reviews a finished pack instead of '
       'building one. Any figure that cannot be tied back is flagged, never smoothed.',
       'finance', 4, 'Finance & Planning', 'Kago Tshutlhedi', 'ktshutlhedi@alphadirect.co.bw',
       saving=420000, fte=2.0, area='finance'),
    _i('SV-01', 'Customers can see their own claim',
       'A tracker link by SMS that shows where the claim is, so the call never has to be made.',
       'service', 4, 'Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw',
       saving=240000, fte=1.5, service=True, area='claims'),
    _i('SV-02', 'Answer the top ten questions without a call',
       'Balance, next debit, policy document, claim status, certificate — self-service on the '
       'app, with a person one tap away.',
       'service', 4, 'Operations', 'Bharath Balasubramanian', 'bbalasubramanian@alphadirect.co.bw',
       saving=300000, fte=2.0, service=True, area='operations'),
    _i('SV-03', 'One service promise, measured daily',
       'First response, time to settle, complaints reopened — on this board every morning, '
       'by department.',
       'service', 4, 'Operations', 'Bharath Balasubramanian', 'bbalasubramanian@alphadirect.co.bw',
       saving=0, fte=0.0, service=True, area='operations'),
    _i('AC-01', 'Move freed people to winning customers',
       'People released from Admin, Finance and Claims retrain onto acquisition and retention '
       'rather than leaving. The plan is per department and visible here.',
       'acquisition', 4, 'Human Capital', 'Unami Butale', 'ubutale@alphadirect.co.bw',
       saving=0, fte=0.0, service=True, area='hris'),
    _i('AC-02', 'Self-serve quote and buy',
       'A customer or broker completes a quote and pays without an underwriter touching it. '
       'This is the step that changes the shape of the company.',
       'acquisition', 4, 'Sales & Marketing', TBC, '',
       saving=900000, fte=5.0, vendor=True, service=True, area='sales'),
]


# Target shape per department. Headcount and cost are refreshed from payroll by
# the nightly pulse — only the target and the narrative live here.
DEPARTMENT_PLANS = [
    # dept, manager, email, target heads, move to acquisition, automation note, manual note
    ('Finance & Planning', 'Pako Kago', 'pkago@alphadirect.co.bw', 11, 3,
     'Refund bridge, collections loop, month close.',
     'Bank matching, refund keying, RealPay exception hunting, commission release.'),
    ('Claims', 'Wangu Moses', 'wmoses@alphadirect.co.bw', 8, 2,
     'Full claims chain: event in, rules, letters, purchase orders.',
     'Re-typing assessments, drafting letters, chasing payment status.'),
    ('Underwriting', 'Gomolemo Sebudula', 'gsebudula@alphadirect.co.bw', 9, 3,
     'Renewals, re-rating, referral rules, endorsement events.',
     'Capture on the big policy form, renewal chasing, endorsement typing.'),
    ('Software Development', 'Shingidzano Lesetedi', 'slesetedi@theriskco.com', 11, 0,
     'This team builds the removal — it does not shrink first.',
     ''),
    ('Admin & IT', TBC, '', 6, 2,
     'Access lifecycle, licence sync and tracker health already automated.',
     'Manual access requests, device tracking.'),
    ('Business Development', TBC, '', 6, 0,
     'Commission release and broker statements.',
     'Statement preparation by hand.'),
    ('Sales & Marketing', TBC, '', 4, 3,
     'Lead worklists; self-serve quote and buy.',
     'Manual lead follow-up.'),
    ('Compliance', 'Kakale Botana', 'kbotana@alphadirect.co.bw', 6, 0,
     'Regulatory pre-fill and evidence packs.',
     'Retyping returns from the mirror.'),
    ('Human Capital', 'Unami Butale', 'ubutale@alphadirect.co.bw', 1, 0,
     'Lifecycle, contracts, leave and digests already automated.',
     ''),
    ('Health Insurance', 'Meduduetso Tlagae', 'mtlagae@alphadirect.co.bw', 5, 0,
     'In scope only once the platform decision is taken.',
     ''),
    ('UniCoin', 'Bakang Taote', 'btaote@insurance.co.bw', 11, 0,
     'Allocation, QC and reporting already largely automated.',
     ''),
    ('Veritas', 'Tshephang Motswagae', 'tshephang@motorliquidators.co.bw', 6, 0,
     'Salvage register and approvals.',
     'Manual salvage tracking.'),
    ('Executive', 'Arun Iyer', 'aiyer@alphadirect.co.bw', 6, 0, '', ''),
    ('Special Projects', TBC, '', 2, 0, '', ''),
    ('Operations', 'Bharath Balasubramanian', 'bbalasubramanian@alphadirect.co.bw', 0, 0,
     'Self-service answers the top ten questions.',
     'Inbound calls that the app could answer.'),
]


def seed(stdout=None) -> dict:
    """Create or refresh the path. Never overwrites progress."""
    from django.utils import timezone

    # Botswana date. date.today() reads the server's UTC clock and would set a
    # blocked-since one day out, which feeds the blocking score.
    today = timezone.localdate()
    created = updated = 0
    for row in INITIATIVES:
        code = row.pop('code')
        defaults = dict(row)
        # A step that is already waiting on somebody starts as BLOCKED, with
        # the clock running from today. Seeding it as "not started" would hide
        # the two vendor dependencies that are the real risk to the deadline.
        if defaults.get('blocked_on'):
            defaults['status'] = Initiative.Status.BLOCKED
            defaults['blocked_since'] = today
        obj, was_created = Initiative.objects.get_or_create(code=code, defaults=defaults)
        if not was_created:
            # ONLY wording, money and dates are refreshed. Owner, status,
            # percent and blocked_on are STATE, not content: the entrypoint
            # reseeds on every deploy, so setattr-ing everything would put a
            # person the CFO removed back as owner, and revert a TBC he had
            # filled in — silently, every time we shipped anything.
            for field in REFRESHABLE_FIELDS:
                setattr(obj, field, row[field])
            obj.save(update_fields=[*REFRESHABLE_FIELDS, 'updated_at'])
            updated += 1
        else:
            created += 1
        row['code'] = code

    plans_created = plans_updated = 0
    for (dept, manager, email, target, realloc, note, manual) in DEPARTMENT_PLANS:
        obj, was_created = DepartmentPlan.objects.get_or_create(
            department=dept,
            defaults=dict(manager_name=manager, manager_email=email,
                          headcount_target=target, reallocate_to_acquisition=realloc,
                          automation_note=note, manual_work_note=manual))
        if not was_created:
            obj.manager_name = manager
            obj.manager_email = email
            obj.headcount_target = target
            obj.reallocate_to_acquisition = realloc
            obj.automation_note = note
            obj.manual_work_note = manual
            obj.save()
            plans_updated += 1
        else:
            plans_created += 1

    result = {'initiatives_created': created, 'initiatives_updated': updated,
              'plans_created': plans_created, 'plans_updated': plans_updated}
    if stdout:
        stdout.write(str(result))
    return result
