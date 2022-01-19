from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.MappingListView.as_view(), name='mapping-list'),
    re_path(
        fr"^(?P<mapping>{NAMESPACE_PATTERN})/$",
        views.MappingRetrieveUpdateDestroyView.as_view(),
        name='mapping-detail'
    ),
    re_path(
        fr"^(?P<mapping>{NAMESPACE_PATTERN})/collection-versions/$",
        views.MappingCollectionMembershipView.as_view(),
        name='mapping-collection-versions'
    ),
    re_path(
        fr"^(?P<mapping>{NAMESPACE_PATTERN})/reactivate/$",
        views.MappingReactivateView.as_view(),
        name='mapping-reactivate'
    ),
    re_path(
        fr"^(?P<mapping>{NAMESPACE_PATTERN})/versions/$",
        views.MappingVersionsView.as_view(),
        name='mapping-version-list'
    ),
    re_path(
        fr'^(?P<mapping>{NAMESPACE_PATTERN})/extras/$',
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
        r'^(?P<mapping>{pattern})/(?P<mapping_version>{pattern})/collection-versions/$'.format(
            pattern=NAMESPACE_PATTERN),
        views.MappingCollectionMembershipView.as_view(),
        name='mapping-version-collection-versions'
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
