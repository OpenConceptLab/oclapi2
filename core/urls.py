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
from core.common.utils import get_api_base_url
from core.common.views import RootView, FeedbackView, APIVersionView, ChangeLogView, StandardChecksumView, \
    SmartChecksumView
from core.concepts.views import ConceptsHierarchyAmendAdminView
from core.events.views import GuestEventsView
from core.importers.views import BulkImportView
from core.settings import ENV

api_info = openapi.Info(
        title="OCL API",
        default_version=VERSION,
        description=f"OCL API ({VERSION})",
    )

SchemaView = get_schema_view(
    api_info,
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
    path('swagger/', SchemaView.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', SchemaView.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('healthcheck/', include('core.common.healthcheck.urls')),
    path('admin/reports/authored/', report_views.AuthoredView.as_view(), name='authored-report'),
    path(
        'admin/reports/monthly-usage/job/', report_views.ResourcesReportJobView.as_view(), name='monthly-usage-job'),
    path('admin/concepts/amend-hierarchy/', ConceptsHierarchyAmendAdminView.as_view(), name='concepts-amend-hierarchy'),
    path('$resolveReference/', ReferenceExpressionResolveView.as_view(), name='$resolveReference'),
    path('$checksum/standard/', StandardChecksumView.as_view(), name='$checksum-standard'),
    path('$checksum/smart/', SmartChecksumView.as_view(), name='$checksum-smart'),
    path('users/', include('core.users.urls'), name='users_urls'),
    path('user/', include('core.users.user_urls'), name='current_user_urls'),
    path('orgs/', include('core.orgs.urls'), name='orgs_url'),
    path('sources/', include('core.sources.urls'), name='sources_url'),
    path('repos/', include('core.repos.urls'), name='repos_url'),
    path('url-registry/', include('core.url_registry.urls'), name='url_registry_url'),

    # TODO: require FHIR subdomain
    path('fhir/', include('core.fhir.urls'), name='fhir_urls'),
    path('fhir/CodeSystem/', include('core.code_systems.urls'), name='code_systems_urls'),
    path('fhir/CodeSystem', include('core.code_systems.urls'), name='code_systems_urls_no_slash'),
    path('fhir/ValueSet/', include('core.value_sets.urls'), name='value_sets_urls'),
    path('fhir/ValueSet', include('core.value_sets.urls'), name='value_sets_urls_no_slash'),
    path('fhir/ConceptMap/', include('core.concept_maps.urls'), name='concept_maps_urls'),
    path('fhir/ConceptMap', include('core.concept_maps.urls'), name='concept_maps_urls_no_slash'),

    path('collections/', include('core.collections.urls'), name='collections_urls'),
    path('concepts/$match/', concept_views.MetadataToConceptsListView.as_view(), name='$match-concepts'),
    path('concepts/', concept_views.ConceptListView.as_view(), name='all_concepts_urls'),
    path('mappings/', mapping_views.MappingListView.as_view(), name='all_mappings_urls'),
    path('importers/', include('core.importers.urls'), name='importer_urls'),
    path('indexes/', include('core.indexes.urls'), name='indexes_urls'),
    path('client-configs/', include('core.client_configs.urls'), name='client_config_urls'),
    path('tasks/', include('core.tasks.urls'), name='task_urls'),
    path('events/', GuestEventsView.as_view(), name='guest_events'),
    path(
        'locales/',
        cache_page(
            timeout=60 * 60 * 6, key_prefix='cache_locales'
        )(concept_views.ConceptDefaultLocalesView.as_view()),
        name='ocl-locales'
    ),

    # just for ocldev - DEPRECATED
    path(
        'manage/bulkimport/<str:import_queue>/',
        BulkImportView.as_view(),
        name='bulk_import_detail_url'
    ),
    path('manage/bulkimport/', BulkImportView.as_view(), name='bulk_import_urls'),
    path('toggles/', include('core.toggles.urls'), name='toggles'),
]

if ENV == 'development':
    urlpatterns = [
        path("silk/", include("silk.urls", namespace="silk")),
        *urlpatterns
    ]


handler500 = 'rest_framework.exceptions.server_error'
handler400 = 'rest_framework.exceptions.bad_request'
