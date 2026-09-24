from django.urls import path

from core.capabilities.views import CapabilityConsumeView, CapabilityRefundView

urlpatterns = [
    path('consume/', CapabilityConsumeView.as_view(), name='capabilities-consume'),
    path('refund/', CapabilityRefundView.as_view(), name='capabilities-refund'),
]
