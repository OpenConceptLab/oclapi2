from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.MappingListView.as_view(), name='mapping-list'),
    re_path(
        r"^(?P<mapping>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.MappingRetrieveUpdateDestroyView.as_view(),
        name='mapping-detail'
    ),
    re_path(
        r"^(?P<mapping>{pattern})/versions/$".format(pattern=NAMESPACE_PATTERN),
        views.MappingVersionsView.as_view(),
        name='mapping-version-list'
    ),
    re_path(
        r'^(?P<mapping>{pattern})/extras/$'.format(pattern=NAMESPACE_PATTERN),
        views.MappingExtrasView.as_view(),
        name='mapping-extras'
    ),
    re_path(
        r'^(?P<mapping>{pattern})/extras/(?P<extra>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.MappingExtraRetrieveUpdateDestroyView.as_view(),
        name='mapping-extra'
    ),
    re_path(
        r'^(?P<mapping>{pattern})/(?P<mapping_version>{pattern})/$'.format(pattern=NAMESPACE_PATTERN),
        views.MappingVersionRetrieveView.as_view(),
        name='mapping-version-detail'
    ),
    re_path(
        r'^(?P<mapping>{pattern})/(?P<mapping_version>{pattern})/extras/$'.format(pattern=NAMESPACE_PATTERN),
        views.MappingExtrasView.as_view(),
        name='mapping-extras'
    ),
    re_path(
        r'^(?P<mapping>{pattern})/(?P<mapping_version>{pattern})/extras/(?P<extra>{pattern})/$'.format(
            pattern=NAMESPACE_PATTERN
        ),
        views.MappingExtraRetrieveUpdateDestroyView.as_view(),
        name='mapping-extra'
    ),
]
