from django.conf import settings
from django.db import models


class AwareQuery(models.Model):
    """Audit log — every Alpha Aware question, the SQL the agent ran, the answer.

    Read-only tool, but every use is logged (who asked what, when, and what
    the agent did) so access is inspectable.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='aware_queries')
    question = models.TextField()
    rounds = models.JSONField(default=list, blank=True)   # [{sql, rows, ms} ...]
    answer = models.TextField(blank=True, default='')
    ok = models.BooleanField(default=True)
    duration_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user_id}: {self.question[:60]}'
