from django.urls import path

from core.importers import views

urlpatterns = [
    path(
        'bulk-import-inline/',  # DEPRECATED
        views.BulkImportInlineView.as_view(),
        name='bulk-import-inline'
    ),
    path(
        'bulk-import-parallel-inline/',  # DEPRECATED
        views.BulkImportParallelInlineView.as_view(),
        name='bulk-import-inline'
    ),
    path(
        'bulk-import-parallel-inline/<str:import_queue>/',  # DEPRECATED
        views.BulkImportParallelInlineView.as_view(),
        name='bulk-import-inline'
    ),
    path(
        'bulk-import/file-url/',  # DEPRECATED
        views.BulkImportFileURLView.as_view(),
        name='bulk-import-file-url'
    ),
    path(
        "bulk-import/<str:import_queue>/file-url/",  # DEPRECATED
        views.BulkImportFileURLView.as_view(),
        name='bulk-import-detail-file-url'
    ),
    path(
        'bulk-import/upload/',  # DEPRECATED
        views.BulkImportFileUploadView.as_view(),
        name='bulk-import-file-upload'
    ),
    path(
        "bulk-import/<str:import_queue>/upload/",  # DEPRECATED
        views.BulkImportFileUploadView.as_view(),
        name='bulk-import-detail-file-upload'
    ),

    path('bulk-import/', views.ImportView.as_view(), name='bulk-import'),
    path("bulk-import/<str:import_queue>/", views.ImportView.as_view(), name='bulk-import-detail'),
]
