from django.http import Http404
from rest_framework import status
from rest_framework.generics import RetrieveUpdateDestroyAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.capabilities.constants import (
    CAPABILITY_EXCEEDED_ERROR_CODE, CAPABILITY_ID_BY_NAME, CAPABILITY_NOT_ENTITLED_ERROR_CODE,
)
from core.capabilities.exceptions import CapabilityExceeded
from core.capabilities.models import UsageCounter, UserCapabilityOverride
from core.capabilities.serializers import UserCapabilityOverrideSerializer
from core.common.mixins import ListWithHeadersMixin
from core.common.utils import parse_id
from core.common.views import BaseAPIView


class CapabilityBaseView(APIView):
    permission_classes = (IsAuthenticated,)

    def get_object_id(self):
        """
        Capabilities are only ever created via Keycloak/fixtures (never at request
        time - see UserProfile.check_and_consume_capability), so an unrecognized
        `capability` name is a 404, not something to create on demand. Returns
        (capability_id, None) on success, or (None, error_response) on failure.
        """
        capability_name = self.request.data.get('capability')
        if not capability_name:
            return None, Response({'detail': '"capability" is required.'}, status=status.HTTP_400_BAD_REQUEST)
        capability_id = CAPABILITY_ID_BY_NAME.get(capability_name)
        if capability_id is None:
            return None, Response(status=status.HTTP_404_NOT_FOUND)
        return capability_id, None

    def get_positive_int_units(self, default=1):
        """
        Returns (units, error_response). `units` is untrusted client input - without this,
        a negative value passes straight through to the F('used') + units update
        (check_and_consume_capability / UsageCounter.refund), letting a caller refund their
        own quota (CapabilityConsumeView) or silently charge instead of refund
        (CapabilityRefundView); a non-numeric value raises an uncaught ValueError => 500.
        """
        raw = self.request.data.get('units', default)
        try:
            units = int(raw)
        except (TypeError, ValueError):
            return None, Response({'detail': '"units" must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        if units < 1:
            return None, Response({'detail': '"units" must be a positive integer.'}, status=status.HTTP_400_BAD_REQUEST)
        return units, None

    def get_owned_map_project_and_run(self):
        from core.map_projects.models import AutomatchRun, MapProject

        request = self.request
        map_project = None
        map_project_id = parse_id(request.data.get('map_project', None))
        if map_project_id is not None:
            # .only('id'): these rows only feed the UsageEvent FKs
            map_project = MapProject.objects.filter(id=int(map_project_id), created_by=request.user).only('id').first()

        run = None
        run_id = parse_id(request.data.get('run', None))
        if run_id is not None:
            run_qs = AutomatchRun.objects.filter(id=int(run_id))
            run_qs = run_qs.filter(map_project=map_project) if map_project else run_qs.filter(
                map_project__created_by=request.user)
            run = run_qs.only('id').first()

        return map_project, run


class CapabilityConsumeView(CapabilityBaseView):
    """
    Cross-service check-and-consume. Used by services that don't share oclapi2's
    database (e.g. ocl-ai-assistant, TQ4) — they hold the same bearer token the
    caller authenticated with, so consumption is always scoped to `request.user`;
    no service can consume against another user's ledger.
    """

    def post(self, request):
        capability_name = request.data.get('capability')
        capability_id, error_response = self.get_object_id()
        if error_response:
            return error_response
        units, error_response = self.get_positive_int_units()
        if error_response:
            return error_response
        map_project, run = self.get_owned_map_project_and_run()

        try:
            request.user.check_and_consume_capability(
                capability_id, units=units,
                action=request.data.get('action', ''), algorithm=request.data.get('algorithm'),
                map_project=map_project, run=run,
            )
        except CapabilityExceeded as ex:
            not_entitled = ex.limit is None
            return Response(
                {
                    'detail': f'You do not have access to {capability_name}.' if not_entitled else
                    f'{capability_name} limit reached.',
                    'error_code': CAPABILITY_NOT_ENTITLED_ERROR_CODE.get(capability_name, 'capability_not_entitled')
                    if not_entitled else
                    CAPABILITY_EXCEEDED_ERROR_CODE.get(capability_name, 'capability_limit_reached'),
                    'limit': ex.limit, 'used': ex.used,
                },
                status=status.HTTP_403_FORBIDDEN
            )

        return Response(
            {
                'capability': capability_name,
                'limit': request.user.get_capability_limit(capability_id),
                'used': request.user.get_capability_usage(capability_id),
            },
            status=status.HTTP_200_OK
        )


class CapabilityRefundView(CapabilityBaseView):
    """
    Manual ledger correction (see UsageCounter.refund) - e.g. staff fixing an
    over-count after a bug. Staff-only, and operates on a user given explicitly
    in the request body: this is an admin tool for correcting ANY user's
    ledger, not a per-request refund a service would issue against its own
    caller's ledger (a caller should consume a capability only once the action
    it gates has actually succeeded - see
    UserProfile.check_and_consume_capability - which needs no refund path at
    all, so no cross-service caller needs staff access to reach this).
    """
    permission_classes = (IsAdminUser,)

    def post(self, request):
        username = request.data.get('user')
        if not username:
            return Response({'detail': '"user" is required.'}, status=status.HTTP_400_BAD_REQUEST)
        from core.users.models import UserProfile
        user = UserProfile.objects.filter(username=username).first()
        if not user:
            return Response(status=status.HTTP_404_NOT_FOUND)

        capability_name = request.data.get('capability')
        capability_id, error_response = self.get_object_id()
        if error_response:
            return error_response
        units, error_response = self.get_positive_int_units()
        if error_response:
            return error_response

        UsageCounter.refund(user, capability_id, units=units)

        return Response(
            {
                'user': username,
                'capability': capability_name,
                'limit': user.get_capability_limit(capability_id),
                'used': user.get_capability_usage(capability_id),
            },
            status=status.HTTP_200_OK
        )


class UserCapabilityOverrideBaseView(BaseAPIView):
    permission_classes = (IsAdminUser,)
    serializer_class = UserCapabilityOverrideSerializer
    is_searchable = False
    default_qs_sort_attr = 'capability__name'

    def get_user(self):
        # drf_yasg instantiates the view with no real URL kwargs to introspect the
        # serializer for /swagger/ - self.kwargs.get('user') is None then, so the
        # real lookup below would 404 on every schema build.
        if getattr(self, 'swagger_fake_view', False):
            return None
        if self.kwargs.get('user_is_self'):
            return self.request.user

        from core.users.models import UserProfile
        return get_object_or_404(UserProfile.objects.filter(username=self.kwargs.get('user')))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['user'] = self.get_user()
        return context

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return UserCapabilityOverride.objects.none()
        return self.get_user().capability_overrides


class UserCapabilityOverrideListView(UserCapabilityOverrideBaseView, ListWithHeadersMixin):
    def get_queryset(self):
        return super().get_queryset().select_related('capability').all()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class UserCapabilityOverrideDetailView(UserCapabilityOverrideBaseView, RetrieveUpdateDestroyAPIView):
    def get_capability_id(self):
        capability_id = CAPABILITY_ID_BY_NAME.get(self.kwargs['capability'])
        if capability_id is None:
            raise Http404()
        return capability_id

    def get_object(self, queryset=None):  # pylint: disable=arguments-differ
        return get_object_or_404(
            self.get_queryset().filter(capability_id=self.get_capability_id()).select_related('capability'))

    def update(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj, _created = UserCapabilityOverride.objects.update_or_create(
            user=self.get_user(), capability_id=self.get_capability_id(),
            defaults={'limit': serializer.validated_data['limit']}
        )
        return Response(self.get_serializer(obj).data)

    def destroy(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
