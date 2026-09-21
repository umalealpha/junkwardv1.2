from django.urls import path

from devlog import forgiveness_views, views
from jobs import views as _jobs_views

urlpatterns = [
    path('cfo/build-log/',        views.build_log_day,  name='cfo-build-log'),
    path('cfo/build-log/item/',   views.record_item,    name='cfo-build-log-item'),
    path('cfo/build-log/status/', views.set_item_status, name='cfo-build-log-status'),
    path('cfo/build-log/confirm/', views.confirm_done, name='cfo-build-log-confirm'),
    path('cfo/whoami/',           views.whoami_cfo,     name='cfo-whoami'),
    path('cfo/forgiveness/',      forgiveness_views.forgiveness, name='cfo-forgiveness'),
    path('cfo/jobs/',             _jobs_views.jobs_list,   name='cfo-jobs'),
    path('cfo/jobs/toggle/',      _jobs_views.jobs_toggle, name='cfo-jobs-toggle'),
]
