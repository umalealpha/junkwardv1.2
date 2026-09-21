"""
cost_per_hour.py — Cost-per-Productive-Hour League (wow feature #9).

Joins what each department COSTS (payroll gross) to what it DOES (Time Doctor
productive hours) for a month — a number nobody at ADIC has seen on one screen.

SENSITIVE: exposes payroll cost + individual productivity. The view is gated to
CFO / EXCO / CEO / COO / HR only (reporting.views.IsCostLeagueViewer). Read-only.

Currency: payroll gross is summed as-recorded, so callers should scope to a
single company (the API does, via the top-bar company filter) to avoid mixing
BWP and foreign-entity (e.g. INR) payslips in one total.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

TWO = Decimal('0.01')


def _month_bounds(month: str):
    """'YYYY-MM' -> (year:int, month:int). Raises ValueError on bad input."""
    y, m = month.split('-')
    return int(y), int(m)


def compute_cost_per_hour(month: str, company_id=None) -> dict:
    """Per-department cost / productive-hour for `month` ('YYYY-MM'), optionally
    scoped to one company. Also returns the per-person breakdown."""
    from integrations.models import TimeDoctorDailySnapshot, TimeDoctorUserMap
    from payroll.models import Payslip

    try:
        year, mon = _month_bounds(month)
    except (ValueError, AttributeError):
        return {'month': month, 'error': "month must be 'YYYY-MM'", 'departments': [], 'people': []}

    # 1) Productive hours per Time Doctor user across the month's snapshots.
    #
    # This reads the STORED snapshots on purpose, and must keep doing so. It is
    # called straight from a web request with no cache (reporting/views.py), and
    # a month is up to 31 days — routing it through integrations.td_live would be
    # 31 live Time Doctor pulls inside one request and would time the page out.
    # What keeps the figures honest is the 15:50 SAST catch-up pull in
    # infra/cron/timedoctor-pull.cron, which re-reads each day after the
    # back-filled hours have landed (checklist L28). PRODUCTIVE hours also cannot
    # be floored with hours_for_day.floored, because the permanent record holds
    # TRACKED hours and productive is always <= tracked. If this report ever
    # needs live figures, cache it first.
    hours_by_uid: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    snaps = TimeDoctorDailySnapshot.objects.filter(as_of__year=year, as_of__month=mon)
    from hris import hours_for_day
    for snap in snaps.iterator():
        # Through the ONE door (checklist L27), in its snapshot-only mode. See
        # hris.hours_for_day.stored for why this consumer is allowed it: 31 live
        # pulls inside one uncached request would time the page out, and the
        # permanent record holds TRACKED hours while this needs PRODUCTIVE.
        for u in hours_for_day.stored(snap.as_of, snap.payload):
            uid = u.get('user_id')
            if not uid:
                continue
            hours_by_uid[uid] += Decimal(str(u.get('productive_hours') or 0))

    # 2) Map TD user -> Employee (only confirmed-or-linked maps count).
    emp_hours: dict = defaultdict(lambda: Decimal('0'))
    umaps = TimeDoctorUserMap.objects.exclude(employee=None).select_related('employee')
    for m in umaps:
        h = hours_by_uid.get(m.td_user_id)
        if h:
            emp_hours[m.employee_id] += h

    # 3) Payroll cost per Employee for the month (scoped to company if given).
    payslips = (
        Payslip.objects
        .filter(period__period_name=month)
        .select_related('employee', 'employee__company')
    )
    if company_id:
        payslips = payslips.filter(employee__company_id=company_id)

    emp_cost: dict = defaultdict(lambda: Decimal('0'))
    emp_meta: dict = {}
    for ps in payslips.iterator():
        if not ps.employee_id:
            continue
        emp_cost[ps.employee_id] += (ps.gross_amount or Decimal('0'))
        emp_meta[ps.employee_id] = (ps.employee.full_name, ps.employee.department or 'Unassigned')

    # An employee needs a payslip this month to have a cost (and cost/hour is
    # meaningless without one), so the league is defined by who was paid this
    # month; productive hours attach where the TD user is mapped.
    emp_ids = set(emp_cost.keys())

    # 4) Aggregate by department + build the people rows.
    dept_cost = defaultdict(lambda: Decimal('0'))
    dept_hours = defaultdict(lambda: Decimal('0'))
    dept_heads = defaultdict(int)
    people = []

    for eid in emp_ids:
        name, dept = emp_meta.get(eid, ('(unknown)', 'Unassigned'))
        cost = emp_cost.get(eid, Decimal('0'))
        hours = emp_hours.get(eid, Decimal('0'))
        dept_cost[dept] += cost
        dept_hours[dept] += hours
        dept_heads[dept] += 1
        cph = (cost / hours).quantize(TWO) if hours > 0 else None
        people.append({
            'full_name': name,
            'department': dept,
            'cost_bwp': str(cost.quantize(TWO)),
            'productive_hours': str(hours.quantize(TWO)),
            'cost_per_hour': str(cph) if cph is not None else None,
        })

    departments = []
    for dept in dept_cost:
        c, h = dept_cost[dept], dept_hours[dept]
        departments.append({
            'department': dept,
            'total_cost_bwp': str(c.quantize(TWO)),
            'productive_hours': str(h.quantize(TWO)),
            'headcount': dept_heads[dept],
            'cost_per_productive_hour': str((c / h).quantize(TWO)) if h > 0 else None,
        })

    def _cph_key(row, field):
        v = row.get(field)
        return Decimal(v) if v is not None else Decimal('-1')

    departments.sort(key=lambda r: _cph_key(r, 'cost_per_productive_hour'), reverse=True)
    people.sort(key=lambda r: _cph_key(r, 'cost_per_hour'), reverse=True)

    return {
        'month': month,
        'company_scoped': bool(company_id),
        'departments': departments,
        'people': people,
    }
