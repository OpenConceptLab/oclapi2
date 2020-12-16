from django.urls import path

from . import views

urlpatterns = [
    path('', views.PinListView.as_view(), name='pin-list'),
    path('<int:pin_id>/', views.PinRetrieveDestroyView.as_view(), name='pin-detail'),
]
