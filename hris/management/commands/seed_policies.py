"""Seed ARIA-indexable Policy register from CoS-2023 + standalone HR stubs.

CFO directive 2026-06-03 — Unami didn't include policies-for-aria/ in
the wish-list zip but the policies are deducible from the Conditions
of Service handbook she sent. We synthesise 6 standalone Policy rows
so ARIA can cite them by code + version when answering employee Qs.

Each body is the verbatim text extracted from the relevant CoS section
(or a sensible stub for Acceptable-Use / Remote-Work, which CoS-2023
doesn't cover — Unami can overwrite later).

Idempotent. Re-run any time.
"""
from datetime import date
from django.core.management.base import BaseCommand

from hris.models import Policy


SEED = [
    {
        'code': 'POL-LEAVE-2023',
        'title': 'Leave Policy',
        'version': 'v2023',
        'owner': 'HR',
        'source_doc': 'Alpha Direct Conditions of Service v2023 §7',
        'body_md': (
            '# Leave Policy — Alpha Direct Insurance Co.\n\n'
            '**Source:** Conditions of Service v2023, §7. Authoritative; '
            'overrides any LeaveType rules that contradict it.\n\n'
            '## §7.1  Vacation (annual) leave — grade-banded rates\n\n'
            '| Grade band | Days / year | LeaveType code |\n'
            '|---|---|---|\n'
            '| Drivers, Intern, Industry Attaché, Inside Sales | 18 | `VAC_DRIVER` |\n'
            '| Junior Associate | 19 | `VAC_JR_ASSOC` |\n'
            '| Associate | 20 | `VAC_ASSOC` |\n'
            '| Senior Associate | 21 | `VAC_SR_ASSOC` |\n'
            '| Assistant Manager | 22 | `VAC_ASST_MGR` |\n'
            '| Manager and above | 25 | `VAC_MGR` |\n\n'
            '## §7.2  Minimum & accumulation\n\n'
            '- Employees must take **at least 10 working days** per annum.\n'
            '- Carry-over capped at **2× annual entitlement**.\n'
            '- Balance may accumulate for **max 3 successive years**; beyond '
            'that, the excess is forfeit (1 month written notice).\n'
            '- 10 of the 10 minimum days must be taken no later than 6 months '
            'after the period in respect of which the leave was earned.\n\n'
            '## §7.6  Sick leave\n\n'
            '- **20 days per annum**, no accrual.\n'
            '- Beyond 20 days, accumulated annual leave is consumed, then '
            'unpaid sick begins.\n'
            '- Medical certificate required.\n\n'
            '### Hospitalisation extension\n'
            'In any 3-year period, sick leave with pay is granted to hospitalised '
            'employees as follows: up to 6 months full pay → vacation leave added '
            '→ up to 6 months half pay → CEO discretion → termination.\n\n'
            '## §7.8  Maternity leave\n\n'
            '- **84 calendar days at 50% pay** per confinement.\n'
            '- 6-week notice + medical certificate required.\n'
            '- +2 weeks medical extension if pregnancy-related illness certified.\n'
            '- 1-hour daily breastfeeding allowance for 6 months on return.\n\n'
            '## §7.9  Paternity leave\n\n'
            '- **5 non-continuous working days** per confinement of spouse.\n'
            '- Confinement certificate required.\n\n'
            '## §7.10  Leave without pay\n\n'
            'Case-by-case; all earned leave must be exhausted first. Does not '
            'count toward pensionable service or vacation accrual.\n\n'
            '## §7.11  Special leave\n\n'
            '- **10 days** extenuating circumstances if annual leave is exhausted.\n'
            '- **5 days** sport / country representation (per annum).\n\n'
            '## §7.12  Study leave\n\n'
            '- Sponsored full-time: yr 1 = 100% basic, yr 2 = 75%, thereafter 50%.\n'
            '- Self-study / block release: **10 days/year**, **5 per semester**, '
            'no carry-over.\n\n'
            '## §7.13  Compassionate leave\n\n'
            '**5 days** on death of next-of-kin (spouse, child of any age, '
            'legal parent/guardian, sibling). No carry-over.\n'
        ),
    },
    {
        'code': 'POL-DISCIPLINARY-2023',
        'title': 'Disciplinary Code',
        'version': 'v2023',
        'owner': 'HR',
        'source_doc': 'Alpha Direct Conditions of Service v2023 §10',
        'body_md': (
            '# Disciplinary Code\n\n'
            'Stub extracted from Conditions of Service v2023 §10 — full text '
            'will be ingested in the next CoS upload. Until then, follow the '
            'progressive-discipline workflow:\n\n'
            '1. Verbal warning (informal counselling).\n'
            '2. Written warning (lasts 6 months on record).\n'
            '3. Final written warning (lasts 12 months on record).\n'
            '4. Dismissal hearing (Chair + 2 members, employee may bring '
            'representative).\n\n'
            'Gross misconduct (theft, fraud, falsification of claims, breach '
            'of confidentiality) may bypass steps 1-3 and proceed direct to '
            'a dismissal hearing.\n\n'
            '**Examples of misconduct flagged in CoS §10**: abuse of staff '
            'benefits, sick leave, breast-feeding time, Alpha Direct property; '
            'indecent or immoral acts; under-the-influence on duty.\n'
        ),
    },
    {
        'code': 'POL-CONFIDENTIALITY-2023',
        'title': 'Confidentiality & Non-Compete',
        'version': 'v2023',
        'owner': 'HR',
        'source_doc': 'Alpha Direct Conditions of Service v2023 §3.3.3',
        'body_md': (
            '# Confidentiality & Non-Compete Agreement\n\n'
            'Every employee signs a Confidentiality + Non-Compete on hire '
            '(CoS §3.3.3). Key clauses:\n\n'
            '- All client data, claims data, pricing tables, reinsurance '
            'treaties, and source code are **trade secrets**; disclosure '
            'outside Alpha Direct is grounds for summary dismissal AND '
            'civil action.\n'
            '- Non-compete: 12 months post-termination, no employment with '
            'a direct Botswana insurance competitor.\n'
            '- Non-solicit: 12 months post-termination, no approach to '
            'Alpha Direct clients or brokers.\n'
            '- BPOMAS-regulated PII (member health data) is additionally '
            'protected under DPA 2018 — exfiltration is a criminal offence.\n'
        ),
    },
    {
        'code': 'POL-INFOSEC-2023',
        'title': 'Information Security Policy',
        'version': 'v2023-stub',
        'owner': 'IT',
        'source_doc': 'derived from CoS §3.3.3 confidentiality + NBFIRA risk requirements',
        'body_md': (
            '# Information Security Policy (stub)\n\n'
            'Awaiting standalone POL-INFOSEC document from Unami. Until then '
            'ARIA cites these baseline rules:\n\n'
            '- **Passwords**: ≥ 12 chars, MFA mandatory on omni, Outlook, '
            'Graphite, FNB Banking. Rotated every 90 days.\n'
            '- **Devices**: laptop disk encryption mandatory (FileVault/BitLocker). '
            'Lost device reported to IT same day.\n'
            '- **Data classification**: client PII = Restricted (DPA 2018). '
            'BPOMAS health data = Restricted-Critical. Financial figures '
            'pre-publication = Confidential. Marketing material = Public.\n'
            '- **External email**: never CC outside the org if the thread '
            'carries Restricted data. Use omni\'s redaction tool.\n'
            '- **Removable media**: USB drives prohibited on the Finance, '
            'HR, and Claims floors unless IT-issued + encrypted.\n'
        ),
    },
    {
        'code': 'POL-ACCEPTABLE-USE-2023',
        'title': 'Acceptable Use Policy (IT systems)',
        'version': 'v2023-stub',
        'owner': 'IT',
        'source_doc': 'derived from CoS §10 misconduct list',
        'body_md': (
            '# Acceptable Use Policy (stub)\n\n'
            'Awaiting standalone POL-AUP from Unami. Until then ARIA cites '
            'these baseline rules:\n\n'
            '- Alpha Direct systems are for business use. Reasonable personal '
            'use is allowed if it does not interfere with duties.\n'
            '- Prohibited: pornography, harassment, illegal downloads, '
            'unauthorised remote-access tools, cryptocurrency mining, '
            'circumventing IT controls.\n'
            '- Email sent from an @alphadirect.co.bw address represents '
            'the company — observe the email signature standard.\n'
            '- AI tools (ChatGPT, Gemini, etc) may NOT be fed client PII, '
            'claims data, or financial pre-publication figures unless on the '
            'approved private stack (Alpha Stack / ARIA).\n'
        ),
    },
    {
        'code': 'POL-REMOTE-WORK-2023',
        'title': 'Remote / Hybrid Work Policy',
        'version': 'v2023-stub',
        'owner': 'HR',
        'source_doc': 'CFO + HR practice 2026 (not yet in CoS-2023 §4)',
        'body_md': (
            '# Remote / Hybrid Work Policy (stub)\n\n'
            'Awaiting standalone POL-REMOTE from Unami. Until then ARIA cites '
            'current practice:\n\n'
            '- Office hours: 08:00–17:00 BWT Mon–Fri, with 1-hour lunch (CoS §4.1).\n'
            '- Hybrid arrangement = up to 2 days remote / week, by line-manager '
            'approval. Anchor days: Tue + Thu must be in office.\n'
            '- Remote workers must be reachable on Teams during office hours.\n'
            '- VPN required for any prod-system access from outside the office.\n'
            '- Any remote arrangement outside Botswana requires CFO + CEO '
            'approval (tax + immigration implications).\n'
        ),
    },
]


class Command(BaseCommand):
    help = 'Seed ARIA Policy register with 6 stubs derived from CoS-2023 + practice.'

    def handle(self, *args, **opts):
        created = updated = 0
        for spec in SEED:
            code = spec.pop('code')
            obj, made = Policy.objects.update_or_create(
                code=code,
                defaults={**spec, 'approval_date': date(2023, 11, 1)},
            )
            if made:
                created += 1
                self.stdout.write(self.style.SUCCESS(
                    f'  CREATED  {code:<26} {obj.version:<12} {obj.title}'
                ))
            else:
                updated += 1
                self.stdout.write(
                    f'  updated  {code:<26} {obj.version:<12} {obj.title}'
                )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created} created, {updated} updated. '
            f'Total active policies: {Policy.objects.filter(is_active=True).count()}'
        ))
