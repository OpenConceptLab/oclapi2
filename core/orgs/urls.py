from django.urls import path, re_path

from core.common.constants import NAMESPACE_PATTERN
from core.orgs.models import Organization
from core.users.views import UserListView
from . import views

urlpatterns = [
    re_path(r'^$', views.OrganizationListView.as_view(), name='organization-list'),
    re_path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/$',
        views.OrganizationDetailView.as_view(),
        name='organization-detail'
    ),
    path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/members/$',
        UserListView.as_view(),
        {
            'related_object_type': Organization,
            'related_object_kwarg': 'org',
            'related_object_attribute': 'members'
        }, name='organization-members'),
    path(
        r'^(?P<org>' + NAMESPACE_PATTERN + ')/members/(?P<user>' + NAMESPACE_PATTERN + ')/$',
        views.OrganizationMemberView.as_view(),
        name='organization-member-detail'
    ),
]
