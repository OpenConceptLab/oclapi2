from django.urls import path

from core.indexes import views

urlpatterns = [
    path("apps/populate/", views.PopulateESIndexView.as_view(), name='populate-indexes'),
    path("apps/rebuild/", views.RebuildESIndexView.as_view(), name='rebuild-indexes'),
    path("resources/<str:resource>/", views.ResourceIndexView.as_view(), name='resource-indexes'),
]
