from django.urls import include, path

from core.sources.feeds import SourceFeed
from . import views

urlpatterns = [
    path('', views.SourceListView.as_view(), name='source-list'),
    path('$compare/', views.SourceVersionsComparisonView.as_view(), name='source-version-$compare'),
    path('$changelog/', views.SourceVersionsChangelogView.as_view(), name='source-version-$changelog'),
    path(
        "<str:source>/",
        views.SourceRetrieveUpdateDestroyView.as_view(),
        name='source-detail'
    ),
    path(
        "<str:source>/client-configs/",
        views.SourceClientConfigsView.as_view(),
        name='source-client-configs'
    ),
    path(
        "<str:source>/mapped-sources/",
        views.SourceMappedSourcesListView.as_view(),
        name='source-mapped-sources'
    ),
    path(
        "<str:source>/<str:version>/mapped-sources/",
        views.SourceVersionMappedSourcesListView.as_view(),
        name='source-version-mapped-sources'
    ),
    path(
        "<str:source>/summary/",
        views.SourceSummaryView.as_view(),
        name='source-summary'
    ),
    path(
        "<str:source>/hierarchy/",
        views.SourceHierarchyView.as_view(),
        name='source-hierarchy'
    ),
    path(
        "<str:source>/logo/",
        views.SourceLogoView.as_view(),
        name='source-logo'
    ),
    path('<str:source>/atom/', SourceFeed()),
    path(
        "<str:source>/versions/",
        views.SourceVersionListView.as_view(),
        name='source-version-list'
    ),
    path(
        '<str:source>/latest/',
        views.SourceLatestVersionRetrieveUpdateView.as_view(),
        name='sourceversion-latest-detail'
    ),
    path(
        '<str:source>/latest/summary/',
        views.SourceLatestVersionSummaryView.as_view(),
        name='sourceversion-latest-summary'
    ),
    path(
        '<str:source>/latest/export/',
        views.SourceVersionExportView.as_view(),
        name='sourceversion-latest-export-detail'
    ),
    path("<str:source>/concepts/$clone/", views.SourceConceptsCloneView.as_view()),
    path("<str:source>/concepts/indexes/", views.SourceConceptsIndexView.as_view()),
    path("<str:source>/mappings/indexes/", views.SourceMappingsIndexView.as_view()),
    path("<str:source>/concepts/", include('core.concepts.urls')),
    path("<str:source>/mappings/", include('core.mappings.urls')),
    path(
        "<str:source>/extras/",
        views.SourceExtrasView.as_view(),
        name='source-extras'
    ),
    path(
        "<str:source>/<str:version>/",
        views.SourceVersionRetrieveUpdateDestroyView.as_view(),
        name='source-version-detail'
    ),
    path("<str:source>/<str:version>/concepts/indexes/", views.SourceConceptsIndexView.as_view()),
    path("<str:source>/<str:version>/mappings/indexes/", views.SourceMappingsIndexView.as_view()),
    path(
        '<str:source>/<str:version>/summary/',
        views.SourceVersionSummaryView.as_view(),
        name='source-version-summary'
    ),
    path(
        "<str:source>/extras/<str:extra>/",
        views.SourceExtraRetrieveUpdateDestroyView.as_view(),
        name='source-extra'
    ),
    path(
        '<str:source>/<str:version>/export/',
        views.SourceVersionExportView.as_view(), name='sourceversion-export'
    ),
    path(
        "<str:source>/<str:version>/extras/",
        views.SourceVersionExtrasView.as_view(),
        name='sourceversion-extras'
    ),
    path(
        "<str:source>/<str:version>/concepts/",
        include('core.concepts.urls')
    ),
    path(
        "<str:source>/<str:version>/mappings/",
        include('core.mappings.urls')
    ),
    path(
        '<str:source>/<str:version>/processing/',
        views.SourceVersionProcessingView.as_view(),
        name='sourceversion-processing'
    ),
    path(
        '<str:source>/<str:version>/resources-checksums/',
        views.SourceVersionResourcesChecksumGenerateView.as_view(),
        name='sourceversion-resource-checksums-generate'
    ),
]
