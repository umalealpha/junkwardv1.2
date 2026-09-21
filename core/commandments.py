"""
core/commandments.py — DeepSeek-backed monthly "10 commandments" engine.

CFO directive 2026-05-18: the /commandments page started as a culture
artefact for Finance / Claims / Underwriting. The CFO wants it to
*rotate every month* and to cover six new audiences: HR, Admin, IT,
Healthcare, Insurance, and Software Developers.

Mechanics:
  * Each (category, year_month) pair caches to a MonthlyCommandments
    row. First page-view of a new month triggers a DeepSeek call; every
    subsequent view in that month serves from the DB.
  * Cache key is `f"{category}:{YYYY-MM}"`. Old months are kept for
    audit / nostalgia, never overwritten.
  * On DeepSeek failure (no key / network / malformed JSON) we fall
    back to a curated seed list per category so the page never renders
    blank.

No PII is sent to DeepSeek — only the category name and the month.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from core.ai_assist import DeepSeekUnavailable, deepseek_complete


log = logging.getLogger(__name__)


# ─── Category registry ────────────────────────────────────────────────

@dataclass(frozen=True)
class Category:
    key:        str   # short id; lives in URL params
    label:      str   # display label
    tag:        str   # one-word kicker shown next to label
    tone:       str   # hex colour for the active pill / number badge
    audience:   str   # used in the DeepSeek prompt
    flavour:    str   # tone-of-voice hint for the prompt


CATEGORIES: dict[str, Category] = {
    'finance':      Category('finance',     'Finance',         'numbers',
                             '#F07F00', 'finance & accounting team at an insurer',
                             'spreadsheet-discipline, audit-trail-obsessed, dry wit'),
    'claims':       Category('claims',      'Claims',          'service',
                             '#00C9B7', 'motor + general-insurance claims team',
                             'human-first, evidence-driven, fair to claimants AND to reserves'),
    'underwriting': Category('underwriting','Underwriting',    'risk',
                             '#7C3AED', 'general-insurance underwriting team',
                             'price-the-risk discipline, broker-relationship aware'),
    'hr':           Category('hr',          'Human Resources', 'people',
                             '#16A34A', 'HR team at a growing insurer',
                             'people-first, plain-spoken, allergic to corporate jargon'),
    'admin':        Category('admin',       'Admin',           'operations',
                             '#0EA5E9', 'administration / operations team',
                             'process-oriented, efficient, anti-bureaucracy'),
    'it':           Category('it',          'IT',              'systems',
                             '#6366F1', 'internal IT / infrastructure team at an insurer',
                             'reliability-first, security-second, automation-third'),
    'healthcare':   Category('healthcare',  'Healthcare',      'cover',
                             '#EC4899', 'medical-insurance / healthcare claims team',
                             'compassionate, evidence-based, fast on pre-authorisations'),
    'insurance':    Category('insurance',   'Insurance',       'industry',
                             '#0D1B2A', 'cross-functional insurance professionals',
                             'broad insurance ethics, treating-customers-fairly principles'),
    'software':     Category('software',    'Software Dev',    'engineering',
                             '#F43F5E', 'software engineering team at an insurer',
                             'pragmatic, ship-it-but-test-it, anti-cargo-cult'),
}


# ─── Seed fallbacks ───────────────────────────────────────────────────
# Hand-curated 10 per category. Used when DeepSeek is unreachable AND
# when the month's row hasn't been generated yet. Frontend never has
# to deal with an empty payload.

_SEED: dict[str, list[dict[str, str]]] = {
    'finance': [
        {'short': 'Thou shalt balance — or thou shalt explain',         'long': 'If debits ≠ credits, neither sleep nor email follow.'},
        {'short': 'No JE without a reason',                              'long': 'Description-less entries are how scandals begin.'},
        {'short': 'Reconcile before midday',                             'long': 'Cash that is not reconciled is cash that does not exist.'},
        {'short': 'Match the GL to the cost driver, not the vendor',    'long': 'Vendor names lie. Cost drivers tell the truth.'},
        {'short': 'Approve in Omni, not in WhatsApp',                   'long': 'If it is not in the audit log, it did not happen.'},
        {'short': 'Provisions are not piggy banks',                     'long': 'Smoothing earnings is a hobby for the unemployed.'},
        {'short': 'Honour the month-end cut',                            'long': 'A back-dated JE is a future audit finding.'},
        {'short': 'Pay vendors on the day you said you would',          'long': 'Trust compounds. So does mistrust.'},
        {'short': 'Document the override',                              'long': 'Every breach of policy needs three things: a why, a who, a when.'},
        {'short': 'When in doubt, ask ARIA, then ask the CFO',          'long': 'In that order. The CFO is busy.'},
    ],
    'claims': [
        {'short': 'Pay the genuine; pursue the dodgy',                  'long': 'Both halves matter equally.'},
        {'short': 'Reserves are promises — keep them honest',           'long': 'Over-reserving steals from profit; under-reserving steals from claimants.'},
        {'short': 'A photo beats a phone call',                         'long': 'Evidence first, sympathy second.'},
        {'short': 'Subrogate before you settle',                        'long': 'Recoveries are revenue you have already earned.'},
        {'short': 'The 48-hour rule is not a suggestion',               'long': 'Slow claims become loud claims.'},
        {'short': 'Salvage is not scrap; salvage is money',             'long': 'Sell it before the rust does.'},
        {'short': 'Reinsurance recoveries are not optional',            'long': 'Forgot the cession? Forgot the profit.'},
        {'short': 'Reject in writing or pay in full',                   'long': 'Verbal rejections become ombudsman complaints.'},
        {'short': 'Never argue with the policy wording',                'long': 'The policy wins; learn from it for next renewal.'},
        {'short': 'A repeat claimant is a story, not a number',         'long': 'Read the file. The pattern is usually there.'},
    ],
    'underwriting': [
        {'short': 'Price the risk, not the relationship',               'long': 'Friendship is free. Underpricing is not.'},
        {'short': 'No quote without the data',                           'long': 'Assumptions are how loss ratios drift.'},
        {'short': 'Honour the rating algorithm',                       'long': 'Manual overrides need a signature and a reason.'},
        {'short': 'Concentration is a slow-motion accident',            'long': 'Sector + geography limits exist for a reason.'},
        {'short': 'Reinsurance cessions before binding',                'long': 'The treaty is your seat belt.'},
        {'short': 'Cover what you priced — no more',                     'long': 'Endorsements without re-pricing are gifts.'},
        {'short': 'Mid-term changes get full medicals',                 'long': 'Risk does not pause for paperwork.'},
        {'short': 'A "yes" at renewal earns a 12-month obligation',     'long': 'Underprice for a quarter, regret for a year.'},
        {'short': 'Brokers are partners, not bosses',                    'long': 'Their distribution is not our pricing committee.'},
        {'short': 'Walk away if it does not fit the appetite',          'long': 'No premium is worth a portfolio reshape.'},
    ],
    'hr': [
        {'short': 'Hire for character, train for skill',                 'long': 'Skill is teachable; integrity is not.'},
        {'short': 'Pay on time, every time',                             'long': 'Late payroll is the fastest way to lose trust.'},
        {'short': 'Document every conversation',                         'long': 'Memory rots; HRIS records do not.'},
        {'short': 'Confidentiality is the floor, not the ceiling',       'long': 'A leaked salary is a leaked employee.'},
        {'short': 'Performance reviews are a dialogue',                  'long': 'A surprise rating means you missed eleven months of coaching.'},
        {'short': 'Equity beats equality',                               'long': 'Same rule, different needs — adjust accordingly.'},
        {'short': 'Exit interviews are gifts',                           'long': 'Listen harder than you defend.'},
        {'short': 'Wellness is not a perk; it is hygiene',               'long': 'Burnout is more expensive than a gym subsidy.'},
        {'short': 'Bias is invisible until it is named',                 'long': 'Run the audit. The numbers will surprise you.'},
        {'short': 'When in doubt, ask the lawyer before the manager',    'long': 'Discipline is a process, not a feeling.'},
    ],
    'admin': [
        {'short': 'A clear desk is a clear mind',                        'long': 'The chaos on the surface is rarely the chaos beneath.'},
        {'short': 'Calendars beat memories',                             'long': 'If it is not on the calendar, it is not happening.'},
        {'short': 'Every supplier needs a contact AND a backup',         'long': 'People leave. Vendors do not.'},
        {'short': 'Keys are tracked; access is logged',                  'long': 'Physical security is still security.'},
        {'short': 'The travel policy is not a suggestion',               'long': 'Receipts on the day, not in the week of doom.'},
        {'short': 'Mail twice a day, no more',                           'long': 'Deep work needs unbroken hours.'},
        {'short': 'Office supplies are the canary in the coal mine',     'long': 'When toner is missing, something bigger is missing too.'},
        {'short': 'Templates exist; use them',                           'long': 'Re-inventing a memo is unbilled hours.'},
        {'short': 'Greet every visitor',                                 'long': 'Reception is the first chapter of the brand.'},
        {'short': 'Ask the operator before you ask the manager',         'long': 'The person doing the work knows the work.'},
    ],
    'it': [
        {'short': 'Patch on Tuesday, before the breach on Wednesday',    'long': 'Vulnerabilities age like milk, not wine.'},
        {'short': 'Backups are unverified until you restore',            'long': 'A backup you have not tested is fiction.'},
        {'short': 'Least privilege, always',                             'long': 'Default-deny beats default-allow every audit.'},
        {'short': 'MFA on everything, no exceptions',                    'long': 'The exception is where the breach starts.'},
        {'short': 'Document the runbook, then automate it',              'long': 'If a human runs it monthly, a machine should run it nightly.'},
        {'short': 'Log everything; review what matters',                 'long': 'You cannot debug what you did not record.'},
        {'short': 'Production is not a playground',                      'long': 'Test in staging or test in incidents.'},
        {'short': 'A simple stack beats a clever one',                   'long': 'Cleverness compounds maintenance.'},
        {'short': 'Disasters happen on Friday afternoons',               'long': 'Plan the recovery now, not at 4pm Friday.'},
        {'short': 'Talk to the user before you fix the bug',             'long': 'The bug they reported is rarely the bug they have.'},
    ],
    'healthcare': [
        {'short': 'Pre-authorise within 24 hours',                       'long': 'Delays in cover are delays in care.'},
        {'short': 'Read the medical aid wording, then read it again',    'long': 'Exclusions hide in adverbs.'},
        {'short': 'Listen first; assess second',                         'long': 'The diagnosis they got is rarely the diagnosis they need.'},
        {'short': 'A fair rejection is a clearly written one',           'long': 'Boilerplate denials are appeals waiting to happen.'},
        {'short': 'Pay providers on schedule',                           'long': 'Unhappy hospitals stop pre-authorising.'},
        {'short': 'PMB is not a marketing slogan',                       'long': 'Prescribed Minimum Benefits are statutory; treat them so.'},
        {'short': 'Fraud has a pattern; spot it early',                  'long': 'Out-of-scope procedures + new providers + cash refunds = look harder.'},
        {'short': 'Wellness lowers claims',                              'long': 'Pay for the gym; save on the hospital.'},
        {'short': 'Confidentiality is sacred',                           'long': 'A health record is a marriage, a job, a mortgage.'},
        {'short': 'Compassion is a clinical tool',                       'long': 'How you say no matters more than the no itself.'},
    ],
    'insurance': [
        {'short': 'Treating customers fairly is a verb, not a poster',   'long': 'Audit it monthly, not annually.'},
        {'short': 'The policy is a promise, not a contract',             'long': 'Lawyers wrote the contract; humans bought the promise.'},
        {'short': 'Solvency is the licence to operate',                  'long': 'Capital first, growth second.'},
        {'short': 'Reinsurance is risk-sharing, not risk-dumping',       'long': 'A bad treaty is worse than no treaty.'},
        {'short': 'Innovation lives between regulation and ethics',      'long': 'Push the boundary; never cross the principle.'},
        {'short': 'Brokers earn their commission',                        'long': 'Distribution is hard. Acknowledge it.'},
        {'short': 'Claims experience trumps marketing budget',           'long': 'Renewal is the truest review you will ever get.'},
        {'short': 'Pricing is a moral act',                               'long': 'Over-charge once; lose a customer for a decade.'},
        {'short': 'Data is a duty, not a weapon',                        'long': 'POPIA / DPA are minimums; ethics is the actual standard.'},
        {'short': 'The industry succeeds when claimants do',             'long': 'Loss ratios that hurt customers eventually hurt insurers too.'},
    ],
    'software': [
        {'short': 'Tests first; vibes second',                           'long': 'A green CI is the only "it works on my machine" worth quoting.'},
        {'short': 'Production is the only environment that matters',     'long': 'Staging lies. Logs do not.'},
        {'short': 'Delete more than you add',                            'long': 'Lines of code are inventory; less is faster.'},
        {'short': 'A good name beats a clever abstraction',              'long': 'Future-you reads names; never reads cleverness.'},
        {'short': 'Ship small; ship often',                              'long': 'Big releases hide big risks.'},
        {'short': 'Errors are dialogue, not failures',                   'long': 'Catch, log, message, then decide.'},
        {'short': 'The user is your unit test',                          'long': 'Watch them; do not survey them.'},
        {'short': 'Refactor under green tests, never red',               'long': 'Two changes at once = two bugs at once.'},
        {'short': 'Commits are letters to future-you',                   'long': 'Write the why, not the what.'},
        {'short': 'Pair when stuck; deep-work when flowing',             'long': 'Both modes are correct; rotate them.'},
    ],
}


# ─── DeepSeek prompt assembly ────────────────────────────────────────

def _prompt(category: Category, year: int, month: int) -> str:
    month_name = date(year, month, 1).strftime('%B')
    return (
        f'You are writing the "10 Commandments" for the {category.audience} '
        f'at Alpha Direct Insurance, for {month_name} {year}. The vibe is '
        f'irreverent but the substance is serious — every commandment should '
        f'name an actual behaviour or principle the team should follow. '
        f'Tone: {category.flavour}.\n\n'
        f'Output ONLY valid JSON: {{"commandments":[{{"short":"...","long":"..."}}, ...]}}. '
        f'Exactly 10 entries. Each "short" is ≤ 60 chars, punchy, imperative; '
        f'each "long" is one sentence (≤ 110 chars) that explains the why. '
        f'No headers, no commentary, no markdown.'
    )


def _normalise(items: list[Any]) -> list[dict[str, str]]:
    """Coerce DeepSeek output into 10 well-formed entries."""
    out: list[dict[str, str]] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        short = str(it.get('short') or '').strip()
        long  = str(it.get('long')  or '').strip()
        if not short or not long:
            continue
        out.append({'short': short[:120], 'long': long[:240]})
        if len(out) == 10:
            break
    return out


# ─── Public entry point ──────────────────────────────────────────────

def get_monthly_commandments(category_key: str,
                              year: int | None = None,
                              month: int | None = None) -> dict[str, Any]:
    """Return the 10 commandments for (category, year, month).

    Reads MonthlyCommandments cache first; on miss, calls DeepSeek and
    persists. On DeepSeek failure, returns the curated seed list.
    """
    from core.models import MonthlyCommandments   # late import — avoid app-load cycle

    cat = CATEGORIES.get(category_key)
    if cat is None:
        cat = CATEGORIES['finance']

    today = timezone.localdate()
    year  = year  or today.year
    month = month or today.month
    ym    = f'{year:04d}-{month:02d}'

    row = MonthlyCommandments.objects.filter(category=cat.key, year_month=ym).first()
    if row and row.commandments:
        items = _normalise(row.commandments)
        if len(items) == 10:
            return _envelope(cat, ym, items, source='cache')

    # Try DeepSeek
    items: list[dict[str, str]] = []
    source = 'seed'
    try:
        raw = deepseek_complete(
            _prompt(cat, year, month),
            response_format='json_object',
            timeout=30.0,
        )
        parsed = json.loads(raw) if raw else {}
        items  = _normalise(parsed.get('commandments') or [])
        if len(items) == 10:
            source = 'deepseek'
            # Cache for this month so subsequent loads are cheap + stable.
            MonthlyCommandments.objects.update_or_create(
                category=cat.key, year_month=ym,
                defaults={'commandments': items, 'generated_at': timezone.now()},
            )
    except (DeepSeekUnavailable, json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning('Commandments %s/%s: DeepSeek miss — %s', cat.key, ym, exc)

    if len(items) != 10:
        items = _SEED.get(cat.key, _SEED['finance'])

    return _envelope(cat, ym, items, source=source)


def _envelope(cat: Category, ym: str, items: list[dict[str, str]],
              source: str) -> dict[str, Any]:
    return {
        'category':       cat.key,
        'label':          cat.label,
        'tag':            cat.tag,
        'tone':           cat.tone,
        'year_month':     ym,
        'source':         source,
        'commandments':   [{'n': i + 1, 'short': c['short'], 'long': c['long']}
                            for i, c in enumerate(items)],
    }


def list_categories() -> list[dict[str, str]]:
    """Lightweight registry the frontend uses to render the pill row."""
    return [
        {'key': c.key, 'label': c.label, 'tag': c.tag, 'tone': c.tone}
        for c in CATEGORIES.values()
    ]
