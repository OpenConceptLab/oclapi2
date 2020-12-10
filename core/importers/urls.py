from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from core.importers import views

urlpatterns = [
    re_path(
        r'^bulk-import-inline/$',
        views.BulkImportInlineView.as_view(),
        name='bulk-import-inline'
    ),
    re_path(
        r'^bulk-import-parallel-inline/$',
        views.BulkImportParallelInlineView.as_view(),
        name='bulk-import-inline'
    ),
    re_path(r'^bulk-import/file-url/$', views.BulkImportFileURLView.as_view(), name='bulk-import-file-url'),
    re_path(
        r"^bulk-import/(?P<import_queue>{pattern})/file-url/$".format(pattern=NAMESPACE_PATTERN),
        views.BulkImportFileURLView.as_view(),
        name='bulk-import-detail-file-url'
    ),
    re_path(r'^bulk-import/upload/$', views.BulkImportFileUploadView.as_view(), name='bulk-import-file-upload'),
    re_path(
        r"^bulk-import/(?P<import_queue>{pattern})/upload/$".format(pattern=NAMESPACE_PATTERN),
        views.BulkImportFileUploadView.as_view(),
        name='bulk-import-detail-file-upload'
    ),
    re_path(r'^bulk-import/$', views.BulkImportView.as_view(), name='bulk-import'),
    re_path(
        r"^bulk-import/(?P<import_queue>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.BulkImportView.as_view(),
        name='bulk-import-detail'
    ),
    re_path(r"^populate-indexes/$", views.PopulateESIndexView.as_view(), name='populate-es-indexes'),
    re_path(r"^rebuild-indexes/$", views.RebuildESIndexView.as_view(), name='rebuild-es-indexes'),
]
