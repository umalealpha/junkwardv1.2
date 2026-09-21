from django.urls import path

from . import views

urlpatterns = [
    path('access/', views.aware_access),
    path('ask/', views.aware_ask),
    path('history/', views.aware_history),
    path('reports/', views.aware_reports_list),
    path('reports/download/', views.aware_report_download),
]
