"""
hris/management/commands/seed_hris_from_html.py

Extracts the GRADES and EMPLOYEES JS arrays from the CFO-supplied
HRIS HTML template (formerly TMS Orbit) and creates corresponding
Django records: Grade, payroll.Employee, HRISProfile, CompetencyArea,
PerformanceReview, OKR.

Idempotent — re-running updates existing rows in place. Safe to run
again after the CFO adds the remaining 220-320 group employees to the
HTML file or hands them over via CSV.

Usage:
    venv\\Scripts\\python.exe manage.py seed_hris_from_html
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Company
from hris.models import (
    CompetencyArea, Grade, HRISProfile, OKR, PerformanceReview,
)
from payroll.models import Employee


HTML_PATH = Path(r'C:\Users\PrathapAsus\alpha-finance\hris\templates\hris.html')


COMPETENCY_AREAS = {
    'LD': ('Leadership',
           'Direction setting, team development, accountability.'),
    'BU': ('Business Acumen',
           'Insurance market awareness, commercial judgement.'),
    'RE': ('Results Orientation',
           'Delivery discipline, risk management, follow-through.'),
    'PE': ('People & Collaboration',
           'Interpersonal skills, cross-functional partnership.'),
    'DI': ('Digital Fluency',
           'Tooling, data, automation, AI-leverage.'),
}


def _seed_competencies():
    for i, (code, (name, desc)) in enumerate(COMPETENCY_AREAS.items()):
        CompetencyArea.objects.update_or_create(
            code=code,
            defaults=dict(name=name, description=desc, sort_order=i),
        )


def _load_html() -> str:
    return HTML_PATH.read_text(encoding='utf-8')


def _to_json_like(js_array: str) -> str:
    """Convert a JS object-literal array to JSON.
    Handles: bareword keys (id:), single quotes, trailing commas."""
    s = js_array
    # Quote bareword keys: {nm:"X", id:1} → {"nm":"X", "id":1}
    s = re.sub(r'(?<=[{,\[\s])([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', s)
    # Single → double quotes for strings (best-effort)
    s = re.sub(r"'([^']*)'", r'"\1"', s)
    # Drop trailing commas before ] or }
    s = re.sub(r',\s*([\]}])', r'\1', s)
    return s


def _parse_array(html: str, name: str):
    m = re.search(rf'(?:const|let|var)\s+{name}\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not m:
        raise RuntimeError(f'Array {name} not found in HRIS HTML')
    return json.loads(_to_json_like(m.group(1)))


# Map TMS Orbit company codes to alpha-finance Company entries
COMPANY_LOOKUP = {
    'ADIC':  'Alpha Direct Insurance',
    'ADIIC': 'Alpha Direct Insurance Investments Company',
    'ADIH':  'Alpha Direct Insurance Holdings',
    'ADIL':  'Alpha Direct Insurance Limited',
}


def _company_for(code: str) -> Company | None:
    # First try by name
    if code in COMPANY_LOOKUP:
        c = Company.objects.filter(name=COMPANY_LOOKUP[code]).first()
        if c:
            return c
    # Fall back to first company (single-entity setup)
    return Company.objects.first()


class Command(BaseCommand):
    help = "Seed the HRIS Django models from the HRIS HTML (former TMS Orbit) data."

    @transaction.atomic
    def handle(self, *args, **options):
        html = _load_html()
        self.stdout.write('=== HRIS Seed ===')

        # 1. Competencies
        _seed_competencies()
        self.stdout.write(f'  competency areas: {CompetencyArea.objects.count()}')

        # 2. Grades
        grades = _parse_array(html, 'GRADES')
        for g in grades:
            Grade.objects.update_or_create(
                code=g['code'],
                defaults=dict(
                    name=g.get('name', ''),
                    level=g.get('level', 1),
                    spread=g.get('spread', 40),
                    midpoint=Decimal(str(g.get('midpoint', 0))),
                    is_active=True,
                ),
            )
        self.stdout.write(f'  grades upserted: {len(grades)}')

        # 3. Employees + HRIS profiles
        employees = _parse_array(html, 'EMPLOYEES')
        emp_count = 0
        prof_count = 0
        review_count = 0
        okr_count = 0
        for e in employees:
            full_name = e.get('nm', '').strip()
            if not full_name:
                continue
            company = _company_for(e.get('company', ''))
            # Find or create payroll.Employee by full_name (unique enough in this
            # context; a CSV import flow would use employee_number)
            emp_no = f"HRIS-{e.get('id', '')}"
            emp, _ = Employee.objects.update_or_create(
                employee_number=emp_no,
                defaults=dict(
                    full_name=full_name,
                    department=e.get('dp', ''),
                    job_title=e.get('ps', ''),
                    company=company,
                    hire_date=e.get('hired') or None,
                    status='active',
                    external_ref=f"HRIS:{e.get('id', '')}",
                ),
            )
            emp_count += 1

            # Find manager (lookup by name; ok if absent for now)
            mgr = None
            if e.get('mg'):
                mgr = Employee.objects.filter(full_name=e['mg']).first()

            grade = Grade.objects.filter(code=e.get('grade', '')).first()
            profile, _ = HRISProfile.objects.update_or_create(
                employee=emp,
                defaults=dict(
                    grade=grade,
                    manager=mgr,
                    location=e.get('loc', ''),
                    gender={'Male': 'M', 'Female': 'F'}.get(e.get('gn', ''), ''),
                    demographic_marker=e.get('tribe', ''),
                    initials=e.get('img', '')[:4],
                ),
            )
            prof_count += 1

            # Latest performance review (uses TMS Orbit's comp/vals/pot scores)
            if e.get('comp'):
                PerformanceReview.objects.update_or_create(
                    profile=profile, period='FY24',
                    defaults=dict(
                        competency_scores=e.get('comp', {}),
                        values_scores=e.get('vals', []),
                        potential_scores=e.get('pot', []),
                        status='finalised',
                    ),
                )
                review_count += 1

            # OKRs
            for okr in (e.get('okrs') or []):
                OKR.objects.update_or_create(
                    profile=profile,
                    period='FY24',
                    name=okr.get('nm', ''),
                    defaults=dict(
                        weight_pct=Decimal(str(okr.get('w', 0))),
                        score_h1=Decimal(str(okr.get('s1', 0))),
                        score_h2=Decimal(str(okr.get('s2', 0))),
                    ),
                )
                okr_count += 1

        self.stdout.write(f'  employees upserted: {emp_count}')
        self.stdout.write(f'  HRIS profiles: {prof_count}')
        self.stdout.write(f'  performance reviews: {review_count}')
        self.stdout.write(f'  OKRs: {okr_count}')
        self.stdout.write(self.style.SUCCESS('HRIS seed complete.'))
