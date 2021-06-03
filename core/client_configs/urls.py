from django.urls import path

from core.client_configs import views

urlpatterns = [
    path('<int:id>/', views.ClientConfigView.as_view(), name='client-config-detail'),
    path('<str:resource>/templates/', views.ResourceTemplatesView.as_view(), name='client-config-templates'),
]
