from django.urls import re_path, include

from core.common.constants import NAMESPACE_PATTERN
from core.users.views import UserListView
from . import views

urlpatterns = [
    re_path(r'^$', views.OrganizationListView.as_view(), name='organization-list'),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/$',
        views.OrganizationDetailView.as_view(),
        name='organization-detail'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/overview/$',
        views.OrganizationOverviewView.as_view(),
        name='organization-overview'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/client-configs/$',
        views.OrganizationClientConfigsView.as_view(),
        name='organization-client-configs'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/logo/$',
        views.OrganizationLogoView.as_view(),
        name='organization-logo'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/extras/$',
        views.OrganizationExtrasView.as_view(),
        name='organization-extras'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/members/$',
        UserListView.as_view(),
        name='organization-members'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/users/$',
        UserListView.as_view(),
        name='organization-users'
    ),
    re_path(
        r"^(?P<org>{pattern})/extras/(?P<extra>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.OrganizationExtraRetrieveUpdateDestroyView.as_view(),
        name='organization-extra'
    ),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/members/(?P<user>' + NAMESPACE_PATTERN + ')/$',
        views.OrganizationMemberView.as_view(),
        name='organization-member-detail'
    ),
    re_path(r'^(?P<org>' + NAMESPACE_PATTERN + ')/sources/', include('core.sources.urls')),
    re_path(r'^(?P<org>' + NAMESPACE_PATTERN + ')/CodeSystem/', include('core.code_systems.urls'),
            name='code_systems_urls'),
    re_path(r'^(?P<org>' + NAMESPACE_PATTERN + ')/ValueSet/', include('core.value_sets.urls'),
            name='value_sets_urls'),
    re_path(r'^(?P<org>' + NAMESPACE_PATTERN + ')/ConceptMap/', include('core.concept_maps.urls'),
            name='concept_maps_urls'),
    re_path(r'^(?P<org>' + NAMESPACE_PATTERN + ')/collections/', include('core.collections.urls')),
    re_path(r'^(?P<org>' + NAMESPACE_PATTERN + ')/pins/', include('core.pins.urls')),
]
