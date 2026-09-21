"""
hris/skills_models.py — Skills & Gaps Map.

Org-wide skills catalogue (Skill) plus one row per employee x skill
proficiency rating (EmployeeSkill, level 1-5). Powers the Skills & Gaps
Map page: who has which skill across the org, and which skills have too
few proficient holders (single-point-of-failure risk if that person
leaves). See hris/skills_views.py for the API and
frontend/src/app/(dashboard)/hris/skills/page.tsx for the grid.

Wired into hris.models via a re-export so `from hris.models import Skill,
EmployeeSkill` keeps working alongside the rest of the HRIS model surface.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel


class Skill(BaseModel):
    """One entry in the org-wide skills catalogue (HR-curated)."""

    name       = models.CharField(max_length=120, unique=True)
    category   = models.CharField(max_length=60, blank=True, default='')
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering            = ['sort_order', 'name']
        verbose_name        = 'Skill'
        verbose_name_plural = 'Skills'

    def __str__(self):
        return self.name


class EmployeeSkill(BaseModel):
    """One employee's proficiency level (1=novice .. 5=expert) on one Skill."""

    profile = models.ForeignKey(
                  'hris.HRISProfile', on_delete=models.CASCADE,
                  related_name='skills',
              )
    skill   = models.ForeignKey(
                  'hris.Skill', on_delete=models.CASCADE,
                  related_name='holders',
              )
    level   = models.PositiveSmallIntegerField(
                  help_text='Proficiency 1 (novice) - 5 (expert).',
              )
    note    = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['-updated_at']
        unique_together     = [('profile', 'skill')]
        verbose_name        = 'Employee Skill'
        verbose_name_plural = 'Employee Skills'

    def __str__(self):
        return f'{self.profile.employee.full_name} — {self.skill.name}: level {self.level}'
