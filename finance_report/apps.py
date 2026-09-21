from django.apps import AppConfig


class FinanceReportConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'finance_report'
    verbose_name = 'Monthly premium, claims and loss ratio report'
