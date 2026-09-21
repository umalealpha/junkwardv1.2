"""Empty urlconf for the isolated commissions test settings — the model +
service suite makes no HTTP requests, and the real root urlconf pulls in every
app's views (which aren't installed in the minimal test app set)."""
urlpatterns = []
