from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from core.importers import views

urlpatterns = [
    re_path(r'^bulk-import/$', views.BulkImportView.as_view(), name='bulk-import'),
    re_path(
        r"^bulk-import/(?P<import_queue>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.BulkImportView.as_view(),
        name='bulk-import-detail'
    ),
]
