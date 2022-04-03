from django.urls import include, path, re_path
from . import views

urlpatterns = [
    re_path('', include('health_check.urls')),
    path('critical/', views.CriticalHealthcheckView.as_view(), name='critical-healthcheck'),
    path('flower/', views.FlowerHealthcheckView.as_view(), name='flower-healthcheck'),
    path('db/', views.DBHealthcheckView.as_view(), name='db-healthcheck'),
    path('redis/', views.RedisHealthcheckView.as_view(), name='redis-healthcheck'),
    path('es/', views.ESHealthcheckView.as_view(), name='redis-healthcheck'),
    path('celery/', views.CeleryHealthCheckView.as_view(), name='celery-healthcheck'),
    path(
        'celery@default/', views.CeleryDefaultHealthCheckView.as_view(),
        name='celery-default-healthcheck'
    ),
    path(
        'celery@indexing/', views.CeleryIndexingHealthCheckView.as_view(),
        name='celery-indexing-healthcheck'
    ),
    path(
        'celery@concurrent/', views.CeleryConcurrentThreadsHealthCheckView.as_view(),
        name='celery-concurrent-healthcheck'
    ),
    path(
        'celery@bulk_import_0/', views.CeleryBulkImport0HealthCheckView.as_view(),
        name='celery-bulk_import_0-healthcheck'
    ),
    path(
        'celery@bulk_import_1/', views.CeleryBulkImport1HealthCheckView.as_view(),
        name='celery-bulk_import_1-healthcheck'
    ),
    path(
        'celery@bulk_import_2/', views.CeleryBulkImport2HealthCheckView.as_view(),
        name='celery-bulk_import_2-healthcheck'
    ),
    path(
        'celery@bulk_import_3/', views.CeleryBulkImport3HealthCheckView.as_view(),
        name='celery-bulk_import_3-healthcheck'
    ),
    path(
        'celery@bulk_import_root/', views.CeleryBulkImportRootHealthCheckView.as_view(),
        name='celery-bulk_import_root-healthcheck'
    ),
]
