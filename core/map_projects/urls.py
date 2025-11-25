from django.urls import path

from . import views

urlpatterns = [
    path('', views.MapProjectListView.as_view(), name='map-project-list'),
    path('<int:project>/', views.MapProjectView.as_view(), name='map-project'),
    path('<int:project>/summary/', views.MapProjectSummaryView.as_view(), name='map-project-summary'),
    path('<int:project>/logs/', views.MapProjectLogsView.as_view(), name='map-project-logs'),
]
