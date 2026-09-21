from django.urls import path

from ci_monitor import views, webhook

urlpatterns = [
    # Signed by GitHub, not by a login — see ci_monitor/webhook.py.
    path('ci/webhook/', webhook.github_webhook, name='ci-webhook'),
    path('cfo/ci/',     views.board,            name='cfo-ci-board'),
]
