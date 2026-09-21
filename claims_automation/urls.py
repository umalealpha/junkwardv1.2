from django.urls import path

from . import views

urlpatterns = [
    path("graphite/events/", views.graphite_event, name="claims-auto-graphite-event"),
    path(
        "graphite/insight/", views.graphite_insight, name="claims-auto-graphite-insight"
    ),
    path("cases/", views.cases, name="claims-auto-cases"),
    path("handovers/", views.handovers_waiting, name="claims-auto-handovers"),
    path("handover/<str:ref>/", views.handover, name="claims-auto-handover"),
    path("cases/<str:ref>/", views.case_detail, name="claims-auto-case"),
    path("letters/<uuid:pk>/html/", views.letter_html, name="claims-auto-letter-html"),
    path("letters/<uuid:pk>/pdf/", views.letter_pdf, name="claims-auto-letter-pdf"),
    path(
        "letters/<uuid:pk>/approve/",
        views.letter_approve,
        name="claims-auto-letter-approve",
    ),
    path(
        "letters/<uuid:pk>/decline/",
        views.letter_decline,
        name="claims-auto-letter-decline",
    ),
]
