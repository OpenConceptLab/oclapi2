from django.urls import path, re_path

from core.orgs.models import Organization
from core.users.views import UserListView
from . import views

urlpatterns = [
    re_path(r'^$', views.OrganizationListView.as_view(), name='organization-list'),
    path('<int:org_id>/', views.OrganizationDetailView.as_view(), name='organization-detail'),
    path(
        '<int:org_id>/members/',
        UserListView.as_view(),
        {
            'related_object_type': Organization,
            'related_object_kwarg': 'org',
            'related_object_attribute': 'members'
        }, name='organization-members'),
    path(
        '<int:org_id>/members/<int:user_id>/',
        views.OrganizationMemberView.as_view(),
        name='organization-member-detail'
    ),
]
