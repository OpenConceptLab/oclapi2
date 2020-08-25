from django.urls import re_path

from core.importers import views

urlpatterns = [
    re_path(r'^bulk-import/$', views.BulkImportView.as_view(), name='bulk-import'),
]
