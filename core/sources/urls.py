from django.urls import re_path, include

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.SourceListView.as_view(), name='source-list'),
    re_path(
        r"^(?P<source>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceRetrieveUpdateDestroyView.as_view(),
        name='source-detail'
    ),
    re_path(
        r"^(?P<source>{pattern})/versions/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceVersionListView.as_view(),
        name='source-version-list'
    ),
    re_path(r"^(?P<source>{pattern})/concepts/".format(pattern=NAMESPACE_PATTERN), include('core.concepts.urls')),
    re_path(
        r"^(?P<source>{pattern})/extras/$".format(pattern=NAMESPACE_PATTERN),
        views.SourceExtrasView.as_view(),
        name='source-extras'
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
]
