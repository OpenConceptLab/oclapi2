from django.urls import path

from . import views


def metadata_routes(prefix, name):
    """A FHIR base's metadata and $versions, with and without a trailing slash: /fhir/'s, or an owner base's."""
    return [
        path(f'{prefix}metadata', views.CapabilityStatementView.as_view(), name=f'{name}-metadata'),
        path(f'{prefix}metadata/', views.CapabilityStatementView.as_view(), name=f'{name}-metadata-slash'),
        path(f'{prefix}$versions', views.VersionsView.as_view(), name=f'{name}-versions'),
        path(f'{prefix}$versions/', views.VersionsView.as_view(), name=f'{name}-versions-slash'),
    ]


urlpatterns = [
    path('', views.CapabilityStatementView.as_view(), name='capability-statement'),
    *metadata_routes('', 'fhir'),
]
