from django.urls import path, include

from core.users.views import UserListView
from . import views

urlpatterns = [
    path('', views.OrganizationListView.as_view(), name='organization-list'),
    path('<str:org>/', views.OrganizationDetailView.as_view(), name='organization-detail'),
    path('<str:org>/overview/', views.OrganizationOverviewView.as_view(), name='organization-overview'),
    path(
        '<str:org>/client-configs/', views.OrganizationClientConfigsView.as_view(), name='organization-client-configs'),
    path('<str:org>/logo/', views.OrganizationLogoView.as_view(), name='organization-logo'),
    path('<str:org>/extras/', views.OrganizationExtrasView.as_view(), name='organization-extras'),
    path('<str:org>/members/', UserListView.as_view(), name='organization-members'),
    path('<str:org>/users/', UserListView.as_view(), name='organization-users'),
    path(
        "<str:org>/extras/<str:extra>/",
        views.OrganizationExtraRetrieveUpdateDestroyView.as_view(), name='organization-extra'),
    path('<str:org>/members/<str:user>/', views.OrganizationMemberView.as_view(), name='organization-member-detail'),
    path(
        '<str:org>/url-registry/', include('core.url_registry.urls'), name='org-url-registry-urls'),
    path('<str:org>/repos/', include('core.repos.urls'), name='org-repos-urls'),
    path('<str:org>/sources/', include('core.sources.urls')),
    path('<str:org>/CodeSystem/', include('core.code_systems.urls'), name='code_systems_urls'),
    path('<str:org>/CodeSystem', include('core.code_systems.urls'), name='code_systems_urls_no_slash'),
    path('<str:org>/ValueSet/', include('core.value_sets.urls'), name='value_sets_urls'),
    path('<str:org>/ValueSet', include('core.value_sets.urls'), name='value_sets_urls_no_slash'),
    path('<str:org>/ConceptMap/', include('core.concept_maps.urls'), name='concept_maps_urls'),
    path('<str:org>/ConceptMap', include('core.concept_maps.urls'), name='concept_maps_urls_no_slash'),
    path('<str:org>/collections/', include('core.collections.urls')),
    path('<str:org>/pins/', include('core.pins.urls')),
    path('<str:org>/events/', include('core.events.urls')),
]
