from django.urls import path

from . import views

urlpatterns = [
    path('', views.MapProjectListView.as_view(), name='map-project-list'),
    path('<int:project>/', views.MapProjectView.as_view(), name='map-project'),
]
