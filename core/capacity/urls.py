from django.urls import path

from core.capacity.views import CapacityConfigView, CapacityConfigHistoryView, CapacityStatusView

urlpatterns = [
    path('config/', CapacityConfigView.as_view(), name='capacity-config'),
    path('config/history/', CapacityConfigHistoryView.as_view(), name='capacity-config-history'),
    path('status/', CapacityStatusView.as_view(), name='capacity-status'),
]
