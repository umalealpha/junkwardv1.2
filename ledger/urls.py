from django.urls import path
from .views import TrialBalanceView

urlpatterns = [
    path('trial-balance/', TrialBalanceView.as_view(), name='trial-balance'),
]
