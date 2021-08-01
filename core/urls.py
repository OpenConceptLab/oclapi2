"""core URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf.urls import url
from django.contrib import admin
from django.urls import path, include, re_path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

import core.concepts.views as concept_views
import core.mappings.views as mapping_views
from core import VERSION
from core.common.constants import NAMESPACE_PATTERN
from core.common.utils import get_api_base_url
from core.common.views import RootView, FeedbackView, APIVersionView, ChangeLogView, LocalesCleanupView
from core.importers.views import BulkImportView
import core.reports.views as report_views

SchemaView = get_schema_view(
    openapi.Info(
        title="OCL API",
        default_version=VERSION,
        description="OCL API ({})".format(VERSION),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
    url=get_api_base_url()
)

urlpatterns = [
    path('', RootView.as_view(), name='root'),
    path('version/', APIVersionView.as_view(), name='api-version'),
    path('changelog/', ChangeLogView.as_view(), name='changelog'),
    path('feedback/', FeedbackView.as_view(), name='feedback'),
    path('locales-cleanup/', LocalesCleanupView.as_view(), name='locales-cleanup'),
    url(r'^swagger(?P<format>\.json|\.yaml)$', SchemaView.without_ui(cache_timeout=0), name='schema-json'),
    url(r'^swagger/$', SchemaView.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    url(r'^redoc/$', SchemaView.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('healthcheck/', include('core.common.healthcheck.urls')),
    path('admin/', admin.site.urls, name='admin_urls'),
    path('admin/reports/monthly-usage/', report_views.MonthlyUsageView.as_view(), name='monthly-usage-report'),
    path('users/', include('core.users.urls'), name='users_urls'),
    path('user/', include('core.users.user_urls'), name='current_user_urls'),
    path('orgs/', include('core.orgs.urls'), name='orgs_url'),
    path('sources/', include('core.sources.urls'), name='sources_url'),
    path('collections/', include('core.collections.urls'), name='collections_urls'),
    path('concepts/', concept_views.ConceptVersionListAllView.as_view(), name='all_concepts_urls'),
    path('mappings/', mapping_views.MappingVersionListAllView.as_view(), name='all_mappings_urls'),
    path('mappings/debug/', mapping_views.MappingDebugRetrieveDestroyView.as_view(), name='mapping-debug'),
    path('importers/', include('core.importers.urls'), name='importer_urls'),
    path('v1-importers/', include('core.v1_importers.urls'), name='v1_importer_urls'),
    path('indexes/', include('core.indexes.urls'), name='indexes_urls'),
    path('client-configs/', include('core.client_configs.urls'), name='client_config_urls'),

    # just for ocldev
    re_path(
        'manage/bulkimport/(?P<import_queue>{pattern})/'.format(pattern=NAMESPACE_PATTERN),
        BulkImportView.as_view(),
        name='bulk_import_detail_url'
    ),
    url('manage/bulkimport/', BulkImportView.as_view(), name='bulk_import_urls'),
]
