from django.urls import re_path, include, path

from core.common.constants import NAMESPACE_PATTERN
from core.sources.feeds import SourceFeed
from . import views

urlpatterns = [
    path('', views.SourceListView.as_view(), name='source-list'),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/$",
        views.SourceRetrieveUpdateDestroyView.as_view(),
        name='source-detail'
    ),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/client-configs/$",
        views.SourceClientConfigsView.as_view(),
        name='source-client-configs'
    ),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/summary/$",
        views.SourceSummaryView.as_view(),
        name='source-summary'
    ),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/hierarchy/$",
        views.SourceHierarchyView.as_view(),
        name='source-hierarchy'
    ),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/logo/$",
        views.SourceLogoView.as_view(),
        name='source-logo'
    ),
    re_path(fr'^(?P<source>{NAMESPACE_PATTERN})/atom/$', SourceFeed()),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/versions/$",
        views.SourceVersionListView.as_view(),
        name='source-version-list'
    ),
    re_path(
        fr'^(?P<source>{NAMESPACE_PATTERN})/latest/$',
        views.SourceLatestVersionRetrieveUpdateView.as_view(),
        name='sourceversion-latest-detail'
    ),
    re_path(
        fr'^(?P<source>{NAMESPACE_PATTERN})/latest/summary/$',
        views.SourceLatestVersionSummaryView.as_view(),
        name='sourceversion-latest-summary'
    ),
    re_path(
        fr'^(?P<source>{NAMESPACE_PATTERN})/latest/export/$',
        views.SourceVersionExportView.as_view(),
        name='sourceversion-latest-export-detail'
    ),
    re_path(fr"^(?P<source>{NAMESPACE_PATTERN})/concepts/indexes/", views.SourceConceptsIndexView.as_view()),
    re_path(fr"^(?P<source>{NAMESPACE_PATTERN})/mappings/indexes/", views.SourceMappingsIndexView.as_view()),
    re_path(fr"^(?P<source>{NAMESPACE_PATTERN})/concepts/", include('core.concepts.urls')),
    re_path(fr"^(?P<source>{NAMESPACE_PATTERN})/mappings/", include('core.mappings.urls')),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/extras/$",
        views.SourceExtrasView.as_view(),
        name='source-extras'
    ),
    re_path(
        r'^(?P<source>{pattern})/(?P<version>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionRetrieveUpdateDestroyView.as_view(),
        name='source-version-detail'
    ),
    re_path(
        r'^(?P<source>{pattern})/(?P<version>{pattern})/summary/$'.format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionSummaryView.as_view(),
        name='source-version-summary'
    ),
    re_path(
        r"^(?P<source>{pattern})/extras/(?P<extra>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceExtraRetrieveUpdateDestroyView.as_view(),
        name='source-extra'
    ),
    re_path(
        r'^(?P<source>{pattern})/(?P<version>{pattern})/export/$'.format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionExportView.as_view(), name='sourceversion-export'
    ),
    re_path(
        r"^(?P<source>{pattern})/(?P<version>{pattern})/extras/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceExtrasView.as_view(),
        name='sourceversion-extras'
    ),
    re_path(
        r"^(?P<source>{pattern})/(?P<version>{pattern})/extras/(?P<extra>{pattern})/$".format(
            pattern=NAMESPACE_PATTERN
        ),
        views.SourceExtraRetrieveUpdateDestroyView.as_view(),
        name='sourceversion-extra'
    ),
    re_path(
        r"^(?P<source>{pattern})/(?P<version>{pattern})/concepts/".format(
            pattern=NAMESPACE_PATTERN
        ),
        include('core.concepts.urls')
    ),
    re_path(
        r"^(?P<source>{pattern})/(?P<version>{pattern})/mappings/".format(
            pattern=NAMESPACE_PATTERN
        ),
        include('core.mappings.urls')
    ),
    re_path(
        r'^(?P<source>{pattern})/(?P<version>{pattern})/processing/$'.format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionProcessingView.as_view(),
        name='sourceversion-processing'
    ),
]
