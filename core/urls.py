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
from django.urls import path, include, re_path
from django.views.decorators.cache import cache_page
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

import core.concepts.views as concept_views
import core.mappings.views as mapping_views
import core.reports.views as report_views
from core import VERSION
from core.collections.views import ReferenceExpressionResolveView
from core.common.constants import NAMESPACE_PATTERN
from core.common.utils import get_api_base_url
from core.common.views import RootView, FeedbackView, APIVersionView, ChangeLogView
from core.concepts.views import ConceptsHierarchyAmendAdminView
from core.importers.views import BulkImportView

SchemaView = get_schema_view(
    openapi.Info(
        title="OCL API",
        default_version=VERSION,
        description=f"OCL API ({VERSION})",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
    url=get_api_base_url()
)

urlpatterns = [
    path('', RootView.as_view(), name='root'),
    path('oidc/', include('mozilla_django_oidc.urls')),
    path('version/', APIVersionView.as_view(), name='api-version'),
    path('changelog/', ChangeLogView.as_view(), name='changelog'),
    path('feedback/', FeedbackView.as_view(), name='feedback'),
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', SchemaView.without_ui(cache_timeout=0), name='schema-json'),
    re_path(r'^swagger/$', SchemaView.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    re_path(r'^redoc/$', SchemaView.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('healthcheck/', include('core.common.healthcheck.urls')),
    path('admin/reports/authored/', report_views.AuthoredView.as_view(), name='authored-report'),
    path('admin/reports/monthly-usage/', report_views.MonthlyUsageView.as_view(), name='monthly-usage-report'),
    path(
        'admin/reports/monthly-usage/job/', report_views.MonthlyUsageReportJobView.as_view(), name='monthly-usage-job'),
    path('admin/concepts/amend-hierarchy/', ConceptsHierarchyAmendAdminView.as_view(), name='concepts-amend-hierarchy'),
    path('$resolveReference/', ReferenceExpressionResolveView.as_view(), name='reference-$resolve'),
    path('users/', include('core.users.urls'), name='users_urls'),
    path('user/', include('core.users.user_urls'), name='current_user_urls'),
    path('orgs/', include('core.orgs.urls'), name='orgs_url'),
    path('sources/', include('core.sources.urls'), name='sources_url'),
    #TODO: require FHIR subdomain
    path('fhir/CodeSystem/', include('core.code_systems.urls'), name='code_systems_urls'),
    path('fhir/ValueSet/', include('core.value_sets.urls'), name='value_sets_urls'),
    path('fhir/ConceptMap/', include('core.concept_maps.urls'), name='concept_maps_urls'),
    path('collections/', include('core.collections.urls'), name='collections_urls'),
    path('concepts/', concept_views.ConceptListView.as_view(), name='all_concepts_urls'),
    path('mappings/', mapping_views.MappingListView.as_view(), name='all_mappings_urls'),
    path('importers/', include('core.importers.urls'), name='importer_urls'),
    path('indexes/', include('core.indexes.urls'), name='indexes_urls'),
    path('client-configs/', include('core.client_configs.urls'), name='client_config_urls'),
    path('tasks/', include('core.tasks.urls'), name='task_urls'),
    path(
        'locales/',
        cache_page(
            timeout=60 * 60 * 6, key_prefix='cache_locales'
        )(concept_views.ConceptDefaultLocalesView.as_view()),
        name='ocl-locales'
    ),

    # just for ocldev
    re_path(
        f'manage/bulkimport/(?P<import_queue>{NAMESPACE_PATTERN})/',
        BulkImportView.as_view(),
        name='bulk_import_detail_url'
    ),
    path('manage/bulkimport/', BulkImportView.as_view(), name='bulk_import_urls'),
]
