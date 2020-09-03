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
from django.urls import path, include
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

import core.concepts.views as concept_views
import core.mappings.views as mapping_views
from core.common.utils import get_base_url
from core.importers.views import BulkImportView

SchemaView = get_schema_view(
    openapi.Info(
        title="OCL API",
        default_version='v2',
        description="OCL API v2",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
    url=get_base_url()
)

urlpatterns = [
    url(r'^swagger(?P<format>\.json|\.yaml)$', SchemaView.without_ui(cache_timeout=0), name='schema-json'),
    url(r'^swagger/$', SchemaView.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    url(r'^redoc/$', SchemaView.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('admin/', admin.site.urls),
    path('users/', include('core.users.urls')),
    path('orgs/', include('core.orgs.urls')),
    path('sources/', include('core.sources.urls')),
    path('collections/', include('core.collections.urls')),
    path('concepts/', concept_views.ConceptVersionListAllView.as_view(), name='all-concepts'),
    path('mappings/', mapping_views.MappingVersionListAllView.as_view(), name='all-mappings'),
    path('importers/', include('core.importers.urls')),
    url('manage/bulkimport/', BulkImportView.as_view(), name='bulk-import')  # just for ocldev
]
