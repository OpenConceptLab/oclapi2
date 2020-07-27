from django.urls import re_path, include

from core.common.constants import NAMESPACE_PATTERN
from core.sources.feeds import SourceFeed
from . import views

urlpatterns = [
    re_path(r'^$', views.SourceListView.as_view(), name='source-list'),
    re_path(
        r"^(?P<source>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceRetrieveUpdateDestroyView.as_view(),
        name='source-detail'
    ),
    re_path(r'^(?P<source>{pattern})/atom/$'.format(pattern=NAMESPACE_PATTERN), SourceFeed()),
    re_path(
        r"^(?P<source>{pattern})/versions/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionListView.as_view(),
        name='source-version-list'
    ),
    re_path(r"^(?P<source>{pattern})/concepts/".format(pattern=NAMESPACE_PATTERN), include('core.concepts.urls')),
    re_path(r"^(?P<source>{pattern})/mappings/".format(pattern=NAMESPACE_PATTERN), include('core.mappings.urls')),
    re_path(
        r"^(?P<source>{pattern})/extras/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceExtrasView.as_view(),
        name='source-extras'
    ),
    re_path(
        r'^(?P<source>{pattern})/(?P<version>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionRetrieveUpdateDestroyView.as_view(),
        name='source-version-detail'
    ),
    re_path(
        r"^(?P<source>{pattern})/extras/(?P<extra>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceExtraRetrieveUpdateDestroyView.as_view(),
        name='source-extra'
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
