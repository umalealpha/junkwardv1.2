"""
Seed the manager objectives (CFO 2026-09-09).

Starts with Kakale Botana — AML/CFT Officer, 5 reports, and TWO tasks in her
entire Omni history before this, both auto-raised "do your monthly feedback".
The CFO's point was not that she is idle; it is that nobody ever gave her a
number, so there was nothing to be behind on.

Every target below was set against a baseline read live off Graphite and Omni on
2026-09-09, recorded in the `note` on each objective so a later reader can see
what the number was when the target was chosen.

NOT hers, deliberately:
  * ROPA / data-protection register — that is Oratile Ria Tlhomelang's; ROPA is
    a Data Protection Act duty, not an AML one (CFO, same day).
  * NBFIRA returns filed on time — Kago Tshutlhedi and Paul Beka (CFO, same day).

Idempotent: re-running updates the targets and wording, never duplicates. Pass
--commit to write; dry-run otherwise.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

# email -> list of objective definitions
OBJECTIVES = {
    'kbotana@alphadirect.co.bw': [
        # ── the live insurance book (Graphite) ───────────────────────────────
        dict(key='kyc-failed-active',
             title='Live policies with failed or missing KYC',
             counter='gph_kyc_failed_active',
             direction='reduce', target=25,
             how_to='Open Graphite, filter live policies where KYC is not compliant, '
                    'and get the documents in or the policy off risk. Start with the '
                    'largest premiums.',
             note='Baseline 2026-09-09: 2,644 active policies carrying BWP '
                  '12,091,734.61 of annual premium. Clears in about two years at 25 '
                  'a week, which is why it starts now.'),
        dict(key='paid-not-issued',
             title='Policies paid for but never issued',
             counter='gph_paid_not_issued',
             direction='reduce', target=15,
             how_to='For each one: either issue the cover the customer paid for, or '
                    'refund them. Both close the item; leaving it does not.',
             note='Baseline 2026-09-09: 729 policies, BWP 4,816,736.95 received '
                  'against cover that never went live. 105 of them started in the '
                  'last 12 months.'),
        dict(key='claims-paid-kyc-failed',
             title='Claims paid this week to a customer who failed KYC',
             counter='gph_claims_paid_kyc_failed_7d',
             direction='nil', target=0,
             how_to='Check before the payment goes out, not after. If one slips '
                    'through, log why on the claim.',
             note='Last 12 months to 2026-09-09: 32 claims, BWP 1,154,006 paid to '
                  'customers whose KYC had failed or was never done.'),
        # ── counterparty due diligence (Omni) ────────────────────────────────
        dict(key='supplier-kyc',
             title='Suppliers screened for AML',
             counter='omni_supplier_kyc_done',
             direction='increase', target=5,
             how_to='Work down the supplier list by spend, biggest first. A record '
                    'with the screening still marked "unchecked" does not count.',
             note='Baseline 2026-09-09: ZERO suppliers had ever been screened.'),
        dict(key='sanctions-screened',
             title='Parties screened against the sanctions lists',
             counter='omni_sanctions_screened_total',
             direction='increase', target=20,
             how_to='Record the list and its version with every search — that is '
                    'what makes the search provable later.',
             note='Register created 2026-09-09; baseline zero.'),
        dict(key='sanctions-match-unreported',
             title='Confirmed sanctions matches not yet reported to the FIA',
             counter='omni_sanctions_match_unreported',
             direction='nil', target=0,
             how_to='The Financial Intelligence Act says a positive match goes to '
                    'the Financial Intelligence Agency without delay. One of these '
                    'open is a regulatory incident, not a backlog item.',
             note='Zero tolerance by design.'),
        dict(key='pep-unassessed',
             title='Screenings where the PEP question was never answered',
             counter='omni_pep_unassessed',
             direction='nil', target=0,
             how_to='Every screening needs a yes or no on politically exposed '
                    'persons. "Not yet assessed" is not an answer.',
             note='NBFIRA treats identifying prominent and influential persons as '
                  'part of the same customer-identification exercise.'),
        dict(key='str-unfiled',
             title='Suspicious transactions detected but not filed with the FIA',
             counter='omni_str_unfiled',
             direction='nil', target=0,
             how_to='File it or close it with a written reason. Neither is optional.',
             note='Register created 2026-09-09; baseline zero.'),
        # ── governance registers (Omni) ──────────────────────────────────────
        dict(key='risk-register',
             title='Risks on the register with an owner and a treatment plan',
             counter='omni_risk_register_complete',
             direction='increase', target=5,
             how_to='A risk needs a named owner and what we are doing about it. A '
                    'title on its own does not count.',
             note='The risk register existed in Omni with ZERO rows before '
                  '2026-09-09. First real risk report the board will have had.'),
        dict(key='regulatory-breaches',
             title='Open regulatory breaches',
             counter='omni_regulatory_breach_open',
             direction='nil', target=0,
             how_to='Log every breach of a rule or licence condition, fix it, and '
                    'record the fix. Separate from data breaches, which are the '
                    'Data Protection Officer’s.',
             note='Register created 2026-09-09; baseline zero.'),
        dict(key='complaints-overdue',
             title='Customer complaints unresolved past 30 days',
             counter='omni_complaints_overdue',
             direction='nil', target=0,
             how_to='Resolve it or escalate it. Thirty days is the standard.',
             note='Register created 2026-09-09; baseline zero.'),
        dict(key='aml-training',
             title='Staff with no valid AML training in the last 12 months',
             counter='omni_aml_training_outstanding',
             direction='reduce', target=10,
             how_to='Run the session, then record who actually sat it. Attendance '
                    'is the evidence, not the invitation.',
             note='NBFIRA requires a regular staff training programme. Nobody had '
                  'a training record in Omni before 2026-09-09.'),
        # ── quarterly: the CFO's own rule ────────────────────────────────────
        dict(key='board-pack',
             title='Quarterly compliance report to EXCO',
             counter='omni_board_pack_outstanding',
             cadence='quarterly', direction='nil', target=0,
             how_to='Upload the pack and send it to EXCO. A report marked sent with '
                    'nothing attached does not count.',
             note='CFO 2026-09-09: due the 5th of the month after every quarter '
                  'ends. First one under this rule: Monday 5 October 2026.'),
    ],

    # ── Oratile Ria Tlhomelang — Data Protection Officer ────────────────────
    # A DIFFERENT case from Kakale, and worth recording so nobody reads these
    # zero baselines as neglect. Oratile was given ten tasks in July and closed
    # nine, marking the tenth honestly partial. The registers below read zero
    # because the ROPA register, the policy library and the vendor register were
    # DEPLOYED on the morning of 2026-09-09 — she had them for four hours. Her
    # problem was never effort; it was that none of the work was checkable, so a
    # task could be ticked "done" with nothing to verify it against.
    #
    # She has no direct reports, so every number here is her own hands. That is
    # why the list is shorter than Kakale's.
    'otlhomelang@alphadirect.co.bw': [
        dict(key='ropa-confirmed',
             title='Processing records (ROPA) confirmed with a lawful basis',
             counter='omni_ropa_confirmed',
             direction='increase', target=15,
             how_to='Open Compliance > ROPA. For each entry check the suggested '
                    'lawful basis, correct it if wrong, then confirm. An entry '
                    'confirmed without a basis does not count.',
             note='Baseline 2026-09-09: 141 entries detected, 0 confirmed — the '
                  'register went live that morning. 15 a week clears it in ten weeks.'),
        dict(key='policies-real',
             title='Real policies in the library, not placeholders',
             counter='omni_policies_real',
             direction='increase', target=3,
             how_to='Open Compliance > Policy Library. Replace a placeholder with '
                    'the actual policy document and set its review date.',
             note='Baseline 2026-09-09: all 29 rows are placeholders and 2 are '
                  'flagged as gaps. Seeded the same morning.'),
        dict(key='vendor-dpa',
             title='Vendors with a signed data-processing agreement',
             counter='omni_vendor_dpa_signed',
             direction='increase', target=3,
             how_to='Add the vendor to the register and attach the signed '
                    'agreement. A vendor listed without the signed agreement is '
                    'a list entry, not a control.',
             note='Baseline 2026-09-09: 0. Her July task "Document processor '
                  'agreements" was marked done on 19 Aug, but there was no '
                  'register to hold the evidence until 9 Sep.'),
        dict(key='dpia-signoff',
             title='Data-protection impact assessments still unsigned',
             counter='omni_dpia_unsigned',
             direction='reduce', target=1,
             how_to='Get all three signatures — DPO, compliance, CFO. A status '
                    'changed to approved without the signatures is not sign-off.',
             note='Baseline 2026-09-09: 2 assessments, neither signed (one draft, '
                  'one with conditions open). Her July task named FOUR and was '
                  'honestly marked partial — this is the one gap that predates '
                  'the new tooling.'),
        dict(key='dsr-overdue',
             title='Data-subject requests past the 30-day legal clock',
             counter='omni_dsr_overdue',
             direction='nil', target=0,
             how_to='Log every request when it arrives. The clock starts on '
                    'receipt, not on the day someone notices it.',
             note='Baseline 2026-09-09: 0 overdue — but also 0 ever logged, so '
                  'this number only becomes meaningful once requests are recorded.'),
        dict(key='breach-unnotified',
             title='Reportable breaches past 72 hours not notified to the regulator',
             counter='omni_breach_unnotified',
             direction='nil', target=0,
             how_to='Log the breach the moment it is discovered, then notify the '
                    'IDPC inside 72 hours and tick it off on the record.',
             note='Zero tolerance. The 72-hour window is the entire purpose of '
                  'the breach register.'),
        dict(key='sop-acknowledged',
             title='Staff who have acknowledged the written procedures',
             counter='omni_sop_ack_staff',
             direction='increase', target=10,
             how_to='Chase the acknowledgements and record them against each '
                    'person. Counts PEOPLE reached, not signatures collected.',
             note='Baseline 2026-09-09: 5 acknowledgements against 229 procedure '
                  'documents. CFO added this on 9 Sep over the objection that it '
                  'is partly a chasing job rather than her own work.'),
        dict(key='dpo-board-pack',
             title='Quarterly data-protection report to EXCO',
             counter='omni_dpo_pack_outstanding',
             cadence='quarterly', direction='nil', target=0,
             how_to='Upload the pack and send it to EXCO. A report marked sent '
                    'with nothing attached does not count.',
             note='Same rule as the AML pack (CFO 2026-09-09): due the 5th of the '
                  'month after each quarter ends. Filed under its OWN kind, so '
                  "Kakale's pack can never discharge this one."),
    ],
}


class Command(BaseCommand):
    help = 'Create or update the standing manager objectives.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Write (else dry-run).')
        parser.add_argument('--email', type=str, default='',
                            help='Limit to one manager.')

    def handle(self, *args, **opts):
        from hris.models import HRISProfile
        from hris.objective_counters import COUNTERS
        from hris.weekly_objective_models import WeeklyObjective
        from payroll.models import Employee

        commit = opts['commit']
        wanted = opts['email'].strip().lower()
        created = updated = 0

        for email, definitions in OBJECTIVES.items():
            if wanted and email.lower() != wanted:
                continue
            employee = Employee.objects.filter(email__iexact=email).first()
            if employee is None:
                self.stderr.write(self.style.ERROR(f'{email}: no employee record'))
                continue
            profile = HRISProfile.objects.filter(employee=employee).first()
            if profile is None:
                self.stderr.write(self.style.ERROR(f'{email}: no HRIS profile'))
                continue

            self.stdout.write(f'{employee.full_name} ({employee.job_title or "no title"})')
            for spec in definitions:
                # A typo in a counter name would otherwise fail silently at
                # 00:00 on a Sunday, which is the worst possible time to find it.
                if spec['counter'] not in COUNTERS:
                    self.stderr.write(self.style.ERROR(
                        f'  ! {spec["key"]}: no counter named {spec["counter"]!r}'))
                    continue
                defaults = {k: v for k, v in spec.items() if k != 'key'}
                defaults.setdefault('cadence', 'weekly')
                if commit:
                    _, was_created = WeeklyObjective.objects.update_or_create(
                        profile=profile, key=spec['key'], defaults=defaults)
                else:
                    was_created = not WeeklyObjective.objects.filter(
                        profile=profile, key=spec['key']).exists()
                created += was_created
                updated += not was_created
                mark = '+' if was_created else '~'
                self.stdout.write(
                    f'  {mark} {spec["key"]}: {defaults["cadence"]}, '
                    f'{spec["direction"]} {spec["target"]}')

        self.stdout.write(self.style.SUCCESS(
            f'{created} created, {updated} updated.'
            + ('' if commit else '  [DRY RUN — pass --commit to write]')))
