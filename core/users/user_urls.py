from django.urls import path, include

from core.orgs import views as orgs_views
from core.repos.views import OrganizationRepoListView
from core.url_registry.views import OrganizationURLRegistryListView
from core.users import views

extra_kwargs = {'user_is_self': True}

# shortcuts for the currently logged-in user
urlpatterns = [
    path(
        '', views.UserDetailView.as_view(), extra_kwargs, name='user-self-detail'
    ),
    path(
        'orgs/', orgs_views.OrganizationListView.as_view(), extra_kwargs, name='user-organization-list'
    ),
    path(
        'extras/',
        views.UserExtrasView.as_view(),
        extra_kwargs,
        name='user-extras'
    ),
    path(
        'orgs/sources/',
        orgs_views.OrganizationSourceListView.as_view(),
        extra_kwargs,
        name='user-organization-source-list'
    ),
    path(
        'orgs/collections/',
        orgs_views.OrganizationCollectionListView.as_view(),
        extra_kwargs,
        name='user-organization-collection-list'
    ),
    path(
        'orgs/repos/',
        OrganizationRepoListView.as_view(),
        extra_kwargs,
        name='user-organization-repo-list',
    ),
    path(
        'orgs/url-registry/',
        OrganizationURLRegistryListView.as_view(),
        extra_kwargs,
        name='user-organization-url-registry-list',
    ),
    path(
        "extras/<str:extra>/",
        views.UserExtraRetrieveUpdateDestroyView.as_view(),
        extra_kwargs,
        name='user-extra'
    ),
    path('sources/', include('core.sources.urls'), extra_kwargs),
    path('collections/', include('core.collections.urls'), extra_kwargs),
    path('repos/', include('core.repos.urls'), extra_kwargs),
    path('url-registry/', include('core.url_registry.urls'), extra_kwargs),
    path('pins/', include('core.pins.urls'), extra_kwargs),
    path('map-projects/', include('core.map_projects.urls'), extra_kwargs),
]
