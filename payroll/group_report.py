from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.html import escape

from core.models import Company
from core.notifications import send_html_with_cfo_cc
from payroll.models import (
    GroupPayrollLine,
    GroupPayrollSnapshot,
    Payslip,
    PayslipComponent,
    PayrollSignOff,
)
from payroll.monthly_pack import _totals_block

VIEWER_LOCAL_PARTS = ('pganesharajah', 'arjuniyer', 'aiyer', 'ubutale', 'lntabeni')
FRONTEND_URL = getattr(settings, 'FRONTEND_URL', 'https://omni.alphadirect.co.bw')


def _email_local(user):
    if not getattr(user, 'email', None):
        return ''
    return user.email.split('@')[0].lower()


def can_view_group_report(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    local = _email_local(user)
    return any(part == local or local.startswith(f'{part}-') for part in VIEWER_LOCAL_PARTS)


def can_regenerate_group_report(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    local = _email_local(user)
    return local == 'pganesharajah' or local.startswith('pganesharajah-')


def payroll_companies():
    ids = Payslip.objects.values_list('company_id', flat=True).distinct()
    return Company.objects.filter(id__in=list(ids)).order_by('name')


def _empty_row(company):
    return {
        'company': company,
        'status': 'not_run',
        'headcount': 0,
        'basic': Decimal('0'),
        'incentive': Decimal('0'),
        'commission': Decimal('0'),
        'employer_contrib': Decimal('0'),
        'gross': Decimal('0'),
        'paye': Decimal('0'),
        'net': Decimal('0'),
        'ctc': Decimal('0'),
        'source_currency': 'BWP',
        'source_gross': None,
        'fx_rate': None,
        'tieout_ok': True,
        'tieout_note': 'No payslips to report',
        'by_department': {},
    }


def _tieout_note(signoff, headcount, gross, net):
    if not signoff:
        return True, 'No sign-off yet to check against'

    notes = []
    signoff_headcount = signoff.headcount or 0
    if headcount != signoff_headcount:
        notes.append(f"Headcount differs from sign-off by {abs(headcount - signoff_headcount)}")

    gross_diff = gross - (signoff.gross_total or Decimal('0'))
    if abs(gross_diff) > Decimal('1.00'):
        notes.append(f"Gross differs from sign-off by P{abs(gross_diff):,.2f}")

    net_diff = net - (signoff.net_total or Decimal('0'))
    if abs(net_diff) > Decimal('1.00'):
        notes.append(f"Net differs from sign-off by P{abs(net_diff):,.2f}")

    if not notes:
        return True, 'Matches sign-off'
    return False, '; '.join(notes)


def _company_lines_data(period):
    contrib_codes = set(
        PayslipComponent.objects.filter(kind='company_contribution').values_list('code', flat=True)
    )
    rows = []
    excluded = []

    for company in payroll_companies():
        slips = (
            Payslip.objects.filter(period=period, company=company)
            .exclude(status='cancelled')
            .select_related('employee')
            .prefetch_related('lines__component')
        )

        if not slips.exists():
            rows.append(_empty_row(company))
            excluded.append(company.name)
            continue

        totals = _totals_block(slips)
        headcount = totals.get('headcount', 0) or 0
        basic = totals.get('basic') or Decimal('0')
        incentive = totals.get('incentive') or Decimal('0')
        commission = totals.get('commission') or Decimal('0')
        gross = totals.get('gross') or Decimal('0')
        paye = totals.get('paye') or Decimal('0')
        net = totals.get('net') or Decimal('0')
        ctc = totals.get('ctc') or Decimal('0')

        employer_contrib = sum(
            (totals.get('by_component', {}).get(code) or Decimal('0'))
            for code in contrib_codes
        )

        departments = {}
        for slip in slips:
            dept_name = (slip.employee.department or 'Unassigned') if slip.employee else 'Unassigned'
            dept = departments.setdefault(dept_name, {'headcount': 0, 'ctc': Decimal('0')})
            dept['headcount'] += 1
            dept['ctc'] += slip.ctc_amount or Decimal('0')
        by_department = {
            dept_name: {'headcount': dept['headcount'], 'ctc': str(dept['ctc'])}
            for dept_name, dept in departments.items()
        }

        signoff = PayrollSignOff.objects.filter(period=period, company=company).first()
        tieout_ok, tieout_note = _tieout_note(signoff, headcount, gross, net)
        if not totals.get('by_component'):
            # Totals-only import (e.g. ADRisk): gross/net/CTC are real, but Omni holds
            # no basic / incentive / commission split — say so rather than show P0.
            tieout_note = f"{tieout_note}. Totals only — no basic/incentive split in Omni"

        status = (
            'final'
            if PayrollSignOff.objects.filter(
                period=period, company=company, status='approved'
            ).exists()
            else 'draft'
        )

        source_currency = 'BWP'
        source_gross = None
        fx_rate = None
        foreign_slips = [s for s in slips if (s.source_currency or 'BWP') != 'BWP']
        if foreign_slips:
            first_foreign = foreign_slips[0]
            source_currency = first_foreign.source_currency or 'BWP'
            source_gross = sum(
                (
                    s.source_gross or Decimal('0')
                    for s in foreign_slips
                    if (s.source_currency or 'BWP') == source_currency
                ),
                Decimal('0'),
            )
            fx_rate = first_foreign.fx_rate_to_bwp

        rows.append(
            {
                'company': company,
                'status': status,
                'headcount': headcount,
                'basic': basic,
                'incentive': incentive,
                'commission': commission,
                'employer_contrib': employer_contrib,
                'gross': gross,
                'paye': paye,
                'net': net,
                'ctc': ctc,
                'source_currency': source_currency,
                'source_gross': source_gross,
                'fx_rate': fx_rate,
                'tieout_ok': tieout_ok,
                'tieout_note': tieout_note,
                'by_department': by_department,
            }
        )

    return rows, excluded


def _sum_decimal(rows, key):
    return sum((row.get(key) or Decimal('0') for row in rows), Decimal('0'))


def _totals_from_rows(included):
    return {
        'headcount': str(sum(row['headcount'] for row in included)),
        'basic': str(_sum_decimal(included, 'basic')),
        'incentive': str(_sum_decimal(included, 'incentive')),
        'commission': str(_sum_decimal(included, 'commission')),
        'employer_contrib': str(_sum_decimal(included, 'employer_contrib')),
        'gross': str(_sum_decimal(included, 'gross')),
        'paye': str(_sum_decimal(included, 'paye')),
        'net': str(_sum_decimal(included, 'net')),
        'ctc': str(_sum_decimal(included, 'ctc')),
    }


def preview_data(period):
    rows, excluded = _company_lines_data(period)
    included = [r for r in rows if r['status'] in ('final', 'draft')]
    return rows, excluded, _totals_from_rows(included)


def generate(period, *, trigger='schedule', user=None):
    with transaction.atomic():
        locked = GroupPayrollSnapshot.objects.filter(period=period).select_for_update()
        max_version = locked.aggregate(Max('version'))['version__max'] or 0
        version = max_version + 1

        rows, excluded = _company_lines_data(period)
        included = [r for r in rows if r['status'] in ('final', 'draft')]
        totals = _totals_from_rows(included)
        totals.update(
            {
                'final_companies': sum(1 for r in included if r['status'] == 'final'),
                'draft_companies': sum(1 for r in included if r['status'] == 'draft'),
                'generated_at': timezone.localtime().isoformat(),
            }
        )

        snap = GroupPayrollSnapshot.objects.create(
            period=period,
            version=version,
            trigger=trigger,
            generated_by=user,
            totals=totals,
            excluded=excluded,
        )

        for row in rows:
            GroupPayrollLine.objects.create(
                snapshot=snap,
                company=row['company'],
                status=row['status'],
                headcount=row['headcount'],
                basic=row['basic'],
                incentive=row['incentive'],
                commission=row['commission'],
                employer_contrib=row['employer_contrib'],
                gross=row['gross'],
                paye=row['paye'],
                net=row['net'],
                ctc=row['ctc'],
                source_currency=row['source_currency'],
                source_gross=row['source_gross'],
                fx_rate=row['fx_rate'],
                tieout_ok=row['tieout_ok'],
                tieout_note=row['tieout_note'],
                by_department=row['by_department'],
            )

        return snap


def _line_dict(line):
    return {
        'company': line.company.name,
        'company_id': str(line.company_id),
        'company_code': line.company.code,
        'status': line.status,
        'headcount': line.headcount,
        'basic': str(line.basic),
        'incentive': str(line.incentive),
        'commission': str(line.commission),
        'employer_contrib': str(line.employer_contrib),
        'gross': str(line.gross),
        'paye': str(line.paye),
        'net': str(line.net),
        'ctc': str(line.ctc),
        'source_currency': line.source_currency,
        'source_gross': str(line.source_gross) if line.source_gross is not None else None,
        'fx_rate': str(line.fx_rate) if line.fx_rate is not None else None,
        'tieout_ok': line.tieout_ok,
        'tieout_note': line.tieout_note,
        'by_department': line.by_department or {},
    }


def snapshot_dict(snap, *, detail=True):
    lines = []
    if detail:
        for line in snap.lines.select_related('company').order_by('company__name'):
            lines.append(_line_dict(line))

    versions = [
        {
            'version': s.version,
            'generated_at': s.created_at.isoformat(),
            'trigger': s.trigger,
        }
        for s in GroupPayrollSnapshot.objects.filter(period=snap.period).order_by('-version')
    ]

    # Latest SAVED report before this month — an off-cycle period with no report
    # in between must not hide it.
    previous_snapshot = (
        GroupPayrollSnapshot.objects.filter(period__start_date__lt=snap.period.start_date)
        .order_by('-period__start_date', '-version')
        .first()
    )
    prior = None
    if previous_snapshot:
        prior = {'period': previous_snapshot.period.period_name, 'totals': previous_snapshot.totals}

    generated_by = None
    if snap.generated_by:
        generated_by = snap.generated_by.get_full_name() or snap.generated_by.username

    return {
        'period': snap.period.period_name,
        'version': snap.version,
        'trigger': snap.trigger,
        'generated_at': snap.totals.get('generated_at', snap.created_at.isoformat()),
        'generated_by': generated_by,
        'totals': snap.totals,
        'excluded': snap.excluded,
        'lines': lines,
        'versions': versions,
        'prior': prior,
    }


def _fmt_decimal(value):
    return f"{Decimal(value):,.2f}"


def notify(snap):
    totals = snap.totals or {}
    ctc = totals.get('ctc', '0')
    basic = totals.get('basic', '0')
    incentive = totals.get('incentive', '0')
    commission = totals.get('commission', '0')
    headcount = totals.get('headcount', '0')
    excluded_count = len(snap.excluded or [])

    subject = f"Group payroll {snap.period.period_name} — cost to company P{_fmt_decimal(ctc)}"

    period_name = escape(snap.period.period_name)
    ctc_display = escape(_fmt_decimal(ctc))
    basic_display = escape(_fmt_decimal(basic))
    incentive_display = escape(_fmt_decimal(incentive))
    commission_display = escape(_fmt_decimal(commission))
    headcount_display = escape(str(headcount))
    url = escape(f"{FRONTEND_URL}/payroll/group-report?period={snap.period.period_name}")

    not_run_html = ''
    if excluded_count:
        not_run_html = (
            '<p style="font-family:Arial,sans-serif;font-size:13px;color:#333;">'
            f"Companies not yet run: {escape(str(excluded_count))}"
            '</p>'
        )

    html = f"""<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f4f4;">
    <table width="100%" cellspacing="0" cellpadding="0" style="background:#f4f4f4;padding:24px 0;">
      <tr>
        <td align="center">
          <table width="640" cellspacing="0" cellpadding="0" style="width:640px;background:#ffffff;border:1px solid #e6e6e6;">
            <tr>
              <td style="background:#0D1B2A;padding:16px 24px;">
                <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:2px;color:#ffffff;text-transform:uppercase;">Alpha Direct · Omni</div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px;">
                <h1 style="font-family:Arial,sans-serif;font-size:20px;color:#F4A623;margin:0 0 20px;">Group payroll report</h1>
                <p style="font-family:Arial,sans-serif;font-size:13px;color:#333;">The group payroll report for {period_name} is ready.</p>
                <table width="100%" cellspacing="0" cellpadding="0" style="margin:16px 0;">
                  <tr>
                    <td style="border:1px solid #e6e6e6;padding:12px;text-align:center;width:20%;"><div style="font-size:11px;color:#666;">Cost to company</div><div style="font-size:16px;font-weight:bold;">P{ctc_display}</div></td>
                    <td style="border:1px solid #e6e6e6;padding:12px;text-align:center;width:20%;"><div style="font-size:11px;color:#666;">Basic</div><div style="font-size:16px;font-weight:bold;">P{basic_display}</div></td>
                    <td style="border:1px solid #e6e6e6;padding:12px;text-align:center;width:20%;"><div style="font-size:11px;color:#666;">Incentives</div><div style="font-size:16px;font-weight:bold;">P{incentive_display}</div></td>
                    <td style="border:1px solid #e6e6e6;padding:12px;text-align:center;width:20%;"><div style="font-size:11px;color:#666;">Commissions</div><div style="font-size:16px;font-weight:bold;">P{commission_display}</div></td>
                    <td style="border:1px solid #e6e6e6;padding:12px;text-align:center;width:20%;"><div style="font-size:11px;color:#666;">Headcount</div><div style="font-size:16px;font-weight:bold;">{headcount_display}</div></td>
                  </tr>
                </table>
                {not_run_html}
                <p style="font-family:Arial,sans-serif;font-size:13px;margin-top:16px;"><a href="{url}" style="color:#0D1B2A;">View report</a></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    to = [f'{local}@alphadirect.co.bw' for local in VIEWER_LOCAL_PARTS]
    return send_html_with_cfo_cc(
        subject=subject,
        html=html,
        to=to,
        text_fallback='',
        cc=None,
        attachments=None,
        cc_cfo=False,
        no_reply=True,
        allow_named_exec=True,
    )
