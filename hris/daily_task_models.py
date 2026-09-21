"""
hris/daily_task_models.py — persistence for the daily HRIS data-task email.

CFO directive 2026-06-25 (made SMART after feedback): so each morning's email can
chase what was NOT actioned yesterday and celebrate what was, instead of re-asking
the same person the same fields every day. One row per calendar day.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel


class HRISDailyTaskRun(BaseModel):
    run_date = models.DateField(unique=True, db_index=True)
    # [{"key": "<emp_id>:<field>", "employee": "<name>", "label": "<label>"}]
    tasks = models.JSONField(default=list, blank=True)
    completeness_pct = models.FloatField(default=0.0)
    focus = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-run_date']
        verbose_name = 'HRIS daily task run'
        verbose_name_plural = 'HRIS daily task runs'

    def __str__(self):
        return f'{self.run_date} — {len(self.tasks or [])} tasks ({self.completeness_pct}%)'
