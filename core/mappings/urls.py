from django.urls import path

from . import views

urlpatterns = [
    path('', views.MappingListView.as_view(), name='mapping-list'),
    path(
        "<str:mapping>/",
        views.MappingRetrieveUpdateDestroyView.as_view(),
        name='mapping-detail'
    ),
    path(
        "<str:mapping>/collection-versions/",
        views.MappingCollectionMembershipView.as_view(),
        name='mapping-collection-versions'
    ),
    path(
        "<str:mapping>/reactivate/",
        views.MappingReactivateView.as_view(),
        name='mapping-reactivate'
    ),
    path(
        "<str:mapping>/versions/",
        views.MappingVersionsView.as_view(),
        name='mapping-version-list'
    ),
    path(
        '<str:mapping>/extras/',
        views.MappingExtrasView.as_view(),
        name='mapping-extras'
    ),
    path(
        '<str:mapping>/extras/<str:extra>/',
        views.MappingExtraRetrieveUpdateDestroyView.as_view(),
        name='mapping-extra'
    ),
    path(
        '<str:mapping>/<str:mapping_version>/',
        views.MappingVersionRetrieveView.as_view(),
        name='mapping-version-detail'
    ),
    path(
        '<str:mapping>/<str:mapping_version>/collection-versions/',
        views.MappingCollectionMembershipView.as_view(),
        name='mapping-version-collection-versions'
    ),
    path(
        '<str:mapping>/<str:mapping_version>/extras/',
        views.MappingExtrasView.as_view(),
        name='mapping-extras'
    ),
    path(
        '<str:mapping>/<str:mapping_version>/extras/<str:extra>/',
        views.MappingExtraRetrieveUpdateDestroyView.as_view(),
        name='mapping-extra'
    ),
]
