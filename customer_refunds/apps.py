from django.apps import AppConfig


class CustomerRefundsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'customer_refunds'
    verbose_name = 'Customer Refunds (Graphite → FNB)'
