from django.urls import re_path, include

from core.collections.feeds import CollectionFeed
from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.CollectionListView.as_view(), name='collection-list'),
    re_path(
        r"^(?P<collection>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionRetrieveUpdateDestroyView.as_view(),
        name='collection-detail'
    ),
    re_path(
        r'^(?P<collection>' + NAMESPACE_PATTERN + ')/client-configs/$',
        views.CollectionClientConfigsView.as_view(),
        name='collection-client-configs'
    ),
    re_path(
        r"^(?P<collection>{pattern})/summary/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionSummaryView.as_view(),
        name='collection-summary'
    ),
    re_path(
        r"^(?P<collection>{pattern})/logo/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionLogoView.as_view(),
        name='collection-logo'
    ),
    re_path(
        r'^(?P<collection>{pattern})/versions/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionListView.as_view(),
        name='collection-version-list'
    ),
    re_path(r'^(?P<collection>{pattern})/concepts/atom/$'.format(pattern=NAMESPACE_PATTERN), CollectionFeed()),
    re_path(
        r'^(?P<collection>{pattern})/latest/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionLatestVersionRetrieveUpdateView.as_view(),
        name='collectionversion-latest-detail'
    ),
    re_path(
        r'^(?P<collection>{pattern})/latest/summary/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionLatestVersionSummaryView.as_view(),
        name='collectionversion-latest-summary'
    ),
    re_path(
        r'^(?P<collection>{pattern})/latest/export/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionExportView.as_view(),
        name='collectionversion-latest-export-detail'
    ),
    re_path(r"^(?P<collection>{pattern})/concepts/".format(pattern=NAMESPACE_PATTERN), include('core.concepts.urls')),
    re_path(r"^(?P<collection>{pattern})/mappings/".format(pattern=NAMESPACE_PATTERN), include('core.mappings.urls')),
    re_path(
        r'^(?P<collection>{pattern})/references/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionReferencesView.as_view(),
        name='collection-references'
    ),
    re_path(
        r'^(?P<collection>{pattern})/references/(?P<reference>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionReferenceView.as_view(),
        name='collection-reference'
    ),
    re_path(
        r"^(?P<collection>{pattern})/extras/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionExtrasView.as_view(),
        name='collection-extras'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionRetrieveUpdateDestroyView.as_view(),
        name='collection-version-detail'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/summary/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionSummaryView.as_view(),
        name='collection-version-summary'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/expansions/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionExpansionsView.as_view(),
        name='collection-version-expansions'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/expansions/(?P<expansion>{pattern})/$'.format(
            pattern=NAMESPACE_PATTERN),
        views.CollectionVersionExpansionView.as_view(),
        name='collection-version-expansion-detail'
    ),
    re_path(
        r"^(?P<collection>{pattern})/extras/(?P<extra>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionExtraRetrieveUpdateDestroyView.as_view(),
        name='collection-extra'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/export/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionExportView.as_view(), name='collectionversion-export'
    ),
    re_path(
        r"^(?P<collection>{pattern})/(?P<version>{pattern})/extras/$".format(pattern=NAMESPACE_PATTERN),
        views.CollectionExtrasView.as_view(),
        name='collectionversion-extras'
    ),
    re_path(
        r"^(?P<collection>{pattern})/(?P<version>{pattern})/extras/(?P<extra>{pattern})/$".format(
            pattern=NAMESPACE_PATTERN
        ),
        views.CollectionExtraRetrieveUpdateDestroyView.as_view(),
        name='collectionversion-extra'
    ),
    re_path(
        r"^(?P<collection>{pattern})/(?P<version>{pattern})/concepts/".format(
            pattern=NAMESPACE_PATTERN
        ),
        include('core.concepts.urls')
    ),
    re_path(
        r"^(?P<collection>{pattern})/(?P<version>{pattern})/mappings/".format(
            pattern=NAMESPACE_PATTERN
        ),
        include('core.mappings.urls')
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/references/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionReferencesView.as_view(),
        name='collectionversion-references'
    ),
    re_path(
        r'^(?P<collection>{pattern})/(?P<version>{pattern})/processing/$'.format(pattern=NAMESPACE_PATTERN),
        views.CollectionVersionProcessingView.as_view(),
        name='collectionversion-processing'
    ),
]
