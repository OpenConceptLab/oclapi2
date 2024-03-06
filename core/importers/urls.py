from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from core.importers import views

urlpatterns = [
    re_path(
        r'^bulk-import-inline/$',  # DEPRECATED
        views.BulkImportInlineView.as_view(),
        name='bulk-import-inline'
    ),
    re_path(
        r'^bulk-import-parallel-inline/$',  # DEPRECATED
        views.BulkImportParallelInlineView.as_view(),
        name='bulk-import-inline'
    ),
    re_path(
        fr'^bulk-import-parallel-inline/(?P<import_queue>{NAMESPACE_PATTERN})/$',  # DEPRECATED
        views.BulkImportParallelInlineView.as_view(),
        name='bulk-import-inline'
    ),
    re_path(
        r'^bulk-import/file-url/$',  # DEPRECATED
        views.BulkImportFileURLView.as_view(),
        name='bulk-import-file-url'
    ),
    re_path(
        fr"^bulk-import/(?P<import_queue>{NAMESPACE_PATTERN})/file-url/$",  # DEPRECATED
        views.BulkImportFileURLView.as_view(),
        name='bulk-import-detail-file-url'
    ),
    re_path(
        r'^bulk-import/upload/$',  # DEPRECATED
        views.BulkImportFileUploadView.as_view(),
        name='bulk-import-file-upload'
    ),
    re_path(
        fr"^bulk-import/(?P<import_queue>{NAMESPACE_PATTERN})/upload/$",  # DEPRECATED
        views.BulkImportFileUploadView.as_view(),
        name='bulk-import-detail-file-upload'
    ),

    re_path(r'^bulk-import/$', views.ImportView.as_view(), name='bulk-import'),
    re_path(
        fr"^bulk-import/(?P<import_queue>{NAMESPACE_PATTERN})/$",
        views.ImportView.as_view(),
        name='bulk-import-detail'
    ),
]
