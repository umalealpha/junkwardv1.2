from django.urls import path

from .api_views import (
    CertificatePdfView, ModuleDetailView, RosterView, SubmitAttemptView,
)


urlpatterns = [
    path("modules/<slug:slug>/",
         ModuleDetailView.as_view(),
         name="training-module"),
    path("modules/<slug:slug>/submit/",
         SubmitAttemptView.as_view(),
         name="training-submit"),
    path("modules/<slug:slug>/roster/",
         RosterView.as_view(),
         name="training-roster"),
    path("attempts/<uuid:pk>/certificate.pdf",
         CertificatePdfView.as_view(),
         name="training-certificate-pdf"),
]
