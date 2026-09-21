"""
records/request_models.py — staff request a physical file from Records, with a
documented approve/deny trail (Tshepo Maswabi bug/feature 2026-08-11:
"request files through Human Capital to Records ... keep a trail of every request
made, approved or denied").

Copies the maker-checker shape of payroll.PayrollAdditionRequest: a request sits
PENDING until Human Capital / Records approves or denies it; every request,
approval and denial is logged (AuditableMixin) and shows on the requester's
/my-requests and the approver's My-Approvals inbox. No physical file moves here —
on approval the Records team issues it with the existing Move feature; this model
is the digital paper trail Records asked for.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class RecordFileRequest(AuditableMixin, BaseModel):
    """One staff request for one physical record, pending HC/Records sign-off."""

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending — with Human Capital / Records'
        APPROVED = 'approved', 'Approved — file may be released'
        DENIED   = 'denied',   'Denied — returned to requester'

    record = models.ForeignKey('records.RecordItem', on_delete=models.PROTECT,
                               related_name='file_requests')
    reason = models.TextField(help_text='Why the requester needs this file.')
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING)

    requested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='record_file_requests')
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by   = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='record_file_requests_decided')
    decided_at   = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-requested_at']
        verbose_name = 'Record File Request'
        verbose_name_plural = 'Record File Requests'

    def __str__(self):
        return f'{self.record.reference} → {self.get_status_display()}'
