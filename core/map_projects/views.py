from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import RetrieveUpdateDestroyAPIView, RetrieveAPIView, CreateAPIView, \
    RetrieveUpdateAPIView
from rest_framework.response import Response

from core.capabilities.constants import CAPABILITY_EXCEEDED_ERROR_CODE, CAPABILITY_NOT_ENTITLED_ERROR_CODE, \
    MAPPER_PROJECTS_CAPABILITY_ID, MAPPER_ROWS_PER_PROJECT_CAPABILITY, MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID
from core.capabilities.exceptions import MapProjectCapacityExceeded
from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin
from core.common.permissions import CanEditConceptDictionary, CanCreateOrgMapProjects, \
    CanUseCustomMapperAlgorithms, HasMapProjectParentOwnership, HasMapProjectCapacity
from core.common.views import BaseAPIView
from core.map_projects.models import MapProject, AutomatchRun
from core.map_projects.serializers import MapProjectCreateUpdateSerializer, \
    MapProjectDetailSerializer, MapProjectSummarySerializer, MapProjectLogsSerializer, MapProjectListSerializer, \
    MapProjectConfigurationsSerializer, AutomatchRunListSerializer, AutomatchRunDetailSerializer, \
    AutomatchRunCreateSerializer, AutomatchRunUpdateSerializer


class MapProjectBaseView(BaseAPIView):
    is_searchable = False
    queryset = MapProject.objects.filter(is_active=True)
    permission_classes = (CanEditConceptDictionary,)
    serializer_class = MapProjectListSerializer

    def get_permissions(self):
        method = self.request.method
        if method == 'POST' and isinstance(self, ConceptDictionaryCreateMixin):
            # Map project creation: no MapProject object exists yet to run
            # CanEditConceptDictionary's object-level check against, so ownership
            # is checked on the URL-scoped parent (org/user) instead.
            permission_classes = (
                HasMapProjectParentOwnership, CanCreateOrgMapProjects, HasMapProjectCapacity,
                CanUseCustomMapperAlgorithms,
            )
        elif method in ('PUT', 'PATCH'):
            # CanCreateOrgMapProjects is deliberately NOT applied here: it gates
            # creating a new org-owned project, not editing one that already exists.
            # Applying it here would make every pre-existing org-owned project
            # read-only for non-preview users, since no group grants mapper_org_projects
            # yet - ownership is still enforced via CanEditConceptDictionary.
            permission_classes = (CanEditConceptDictionary, CanUseCustomMapperAlgorithms)
        elif method == 'DELETE':
            # Same reasoning as PUT/PATCH above: deleting an existing org-owned project
            # isn't "creating" one, so it shouldn't require mapper_org_projects either.
            # CanEditConceptDictionary still gates on the project itself via
            # get_object()/check_object_permissions.
            permission_classes = (CanEditConceptDictionary,)
        else:
            permission_classes = self.permission_classes
        return [permission() for permission in permission_classes]


class MapProjectListView(MapProjectBaseView, ConceptDictionaryCreateMixin, ListWithHeadersMixin):
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return MapProjectCreateUpdateSerializer
        if self.is_verbose():
            return MapProjectDetailSerializer

        return self.serializer_class

    def get_queryset(self):
        queryset = self.queryset.select_related('created_by', 'updated_by', 'organization', 'user')
        if self.request.method == 'GET' and not self.is_verbose():
            queryset = queryset.defer(
                'matches', 'columns', 'candidates', 'analysis', 'logs', 'extras', 'algorithms', 'filters',
                'lookup_config', 'input_locales'
            )
        return self.filter_queryset_by_public_access(self.filter_queryset_by_owner(queryset))

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def create(self, request, **kwargs):  # pylint: disable=unused-argument
        if not self.parent_resource:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        serializer = self.get_serializer(data=MapProject.format_request_data(request.data, self.parent_resource))
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # HasMapProjectCapacity already checked capacity, but that read happens
            # outside any lock, so two concurrent creates from the same user can both
            # pass it before either commits (TOCTOU). Locking the user row here
            # serializes creates from the same user; re-check capacity under the lock
            # before persisting.
            locked_user = type(request.user).objects.select_for_update().get(pk=request.user.pk)
            limit = locked_user.get_capability_limit(MAPPER_PROJECTS_CAPABILITY_ID)
            if limit != 0:
                used = locked_user.map_projects_used
                if limit is None or used >= limit:
                    raise MapProjectCapacityExceeded(limit, used)

            instance = serializer.save(force_insert=True)
            if not instance.pk:
                # MapProjectCreateUpdateSerializer.create() calls MapProject.persist_new(),
                # which catches ValidationError/IntegrityError from full_clean()/save() itself
                # and reports them via self._errors instead of raising - so a validation
                # failure there (e.g. a missing input_file_name) returns an unsaved instance
                # rather than raising. Without this check that unsaved instance would reach
                # log_capability_event() below and crash on the UsageEvent.map_project FK.
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            # mapper.projects is enforced against the live map_projects_used count (see
            # get_capability_usage()), not a monotonic counter - deleting a project
            # correctly frees the slot. This only records the event for attribution.
            request.user.log_capability_event(
                MAPPER_PROJECTS_CAPABILITY_ID, action='create_map_project', map_project=instance
            )

        headers = self.get_success_headers(serializer.data)
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class MapProjectView(MapProjectBaseView, RetrieveUpdateDestroyAPIView):
    serializer_class = MapProjectDetailSerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'

    def get_serializer_class(self):
        if self.request.method == 'PUT':
            return MapProjectCreateUpdateSerializer
        return self.serializer_class

    def update(self, request, *args, **kwargs):
        """Normalize multipart PUT payloads before serializer validation."""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=MapProject.format_request_data(request.data),
            partial=partial
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)


class MapProjectConfigurationsView(MapProjectBaseView, RetrieveAPIView):
    serializer_class = MapProjectConfigurationsSerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'


class MapProjectSummaryView(MapProjectBaseView, RetrieveAPIView):
    serializer_class = MapProjectSummarySerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'


class MapProjectLogsView(MapProjectBaseView, RetrieveAPIView, CreateAPIView):
    serializer_class = MapProjectLogsSerializer
    lookup_url_kwarg = 'project'
    lookup_field = 'project'
    pk_field = 'id'

    def create(self, request, *args, **kwargs):
        map_project = self.get_object()
        new_logs = request.data.get('logs') or {}
        if new_logs:
            map_project.logs = new_logs
            map_project.updated_by = request.user
            map_project.save()
        return Response(status.HTTP_204_NO_CONTENT)


class AutomatchRunBaseView(BaseAPIView):
    """
    Shared base for AutomatchRun endpoints.

    Authorization is always anchored on the parent MapProject, never on the run
    id: the run's BigAutoField is sequential and is NOT a security boundary, so a
    user must not be able to read/patch a run belonging to a project they cannot
    access (ocl_online#105 OQ2). CanEditConceptDictionary is enforced against the
    MapProject in every flow below.
    """
    is_searchable = False
    permission_classes = (CanEditConceptDictionary,)

    def get_client_ip(self):
        forwarded = self.request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip() or None
        return self.request.META.get('REMOTE_ADDR') or None


class AutomatchRunListView(AutomatchRunBaseView, ListWithHeadersMixin):
    """List the runs of a project (GET) and create a run at run start (POST).

    Nested under the owner-scoped project path, e.g.
    ``/orgs/<org>/map-projects/<project>/auto-match-runs/``.
    """
    default_qs_sort_attr = '-started_at'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AutomatchRunCreateSerializer
        return AutomatchRunListSerializer

    def get_map_project(self):
        """Resolve and authorize the parent MapProject from the URL.

        Owner scope (org/user) comes from the nested route and the project id
        from the path; permission is checked against the MapProject itself.
        """
        if getattr(self, '_map_project', None) is None:
            queryset = self.filter_queryset_by_owner(MapProject.objects.filter(is_active=True))
            project = get_object_or_404(queryset, id=self.kwargs.get('project'))
            self.check_object_permissions(self.request, project)
            self._map_project = project
        return self._map_project

    def get_serializer_context(self):
        # The create serializer needs the parent project to validate that a
        # retry's parent_run belongs to the same project (see its validate()).
        context = super().get_serializer_context()
        # drf_yasg instantiates the view without route kwargs while building the
        # schema, so skip parent-project resolution for that synthetic request.
        if self.request.method == 'POST' and not getattr(self, 'swagger_fake_view', False):
            context['map_project'] = self.get_map_project()
        return context

    def get_queryset(self):
        return self.get_map_project().auto_match_runs.select_related('started_by').all()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        map_project = self.get_map_project()
        intended_rows = serializer.validated_data['intended_rows']
        is_retry = bool(serializer.validated_data.get('parent_run'))

        # mapper.match_operations is metered per row-algorithm pair at $match
        # time (MetadataToConceptsListView.post()), not here - a run's actual
        # $match calls (one per algorithm per row-batch, fired as the run
        # executes) already account for every unit this run will use. Consuming
        # it again at run creation double-counted the whole run. rows_per_project
        # is project-scoped and genuinely belongs here: it caps how large a run
        # can even be declared, independent of how much match-operations quota
        # is left.
        if not is_retry:
            rows_limit = request.user.get_capability_limit(MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID)
            if rows_limit != 0:  # explicit grant only - None (unconfigured) is blocked, not uncapped
                rows_used = map_project.rows_used
                if rows_limit is None or rows_used + intended_rows > rows_limit:
                    # rows_limit is None means no override/group row at all - never
                    # entitled - which is a different condition from having a real,
                    # configured allowance that's used up.
                    not_entitled = rows_limit is None
                    return Response(
                        {
                            'detail': 'You do not have a configured row allowance for this project.'
                            if not_entitled else 'Preview row limit for this project reached.',
                            'error_code': CAPABILITY_NOT_ENTITLED_ERROR_CODE[MAPPER_ROWS_PER_PROJECT_CAPABILITY]
                            if not_entitled else CAPABILITY_EXCEEDED_ERROR_CODE[MAPPER_ROWS_PER_PROJECT_CAPABILITY],
                            'limit': rows_limit, 'used': rows_used,
                        },
                        status=status.HTTP_403_FORBIDDEN
                    )

        run = serializer.save(
            map_project=self.get_map_project(),
            started_by=request.user,
            created_by=request.user,
            updated_by=request.user,
            client_ip=self.get_client_ip(),
            client_user_agent=request.META.get('HTTP_USER_AGENT'),
        )
        # The global pre_save uri stamp runs on INSERT before the BigAutoField id
        # is assigned, so the first-persisted uri carries a temp id. Re-save the
        # uri now that the id exists to store the canonical run uri.
        run.save(update_fields=['uri'])
        return Response(
            AutomatchRunDetailSerializer(run, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class AutomatchRunView(AutomatchRunBaseView, RetrieveUpdateAPIView):
    """Fetch a single run (GET) and update its lifecycle fields (PATCH).

    Addressed by run id at the top level, e.g. ``/auto-match-runs/<run>/``; the
    Mapper UI PATCHes progress/completion here without threading the owner path.
    """
    # Updates are PATCH-only (the run-start snapshot is immutable, so a whole-object
    # PUT has no meaning here). get_object is fully overridden below for project-scoped
    # authz, so lookup_field/pk_field are unused.
    http_method_names = ['get', 'patch', 'head', 'options']
    lookup_url_kwarg = 'run'

    def get_serializer_class(self):
        if self.request.method == 'PATCH':
            return AutomatchRunUpdateSerializer
        return AutomatchRunDetailSerializer

    def get_queryset(self):
        return AutomatchRun.objects.filter(is_active=True).select_related('started_by', 'map_project')

    def get_object(self, queryset=None):  # pylint: disable=arguments-differ
        run = get_object_or_404(self.get_queryset(), id=self.kwargs.get(self.lookup_url_kwarg))
        # Project-scoped authorization: resolve to the parent project and enforce
        # ownership there (ocl_online#105 OQ2). The run id is not a boundary.
        self.check_object_permissions(self.request, run.map_project)
        return run

    def update(self, request, *args, **kwargs):
        """Persist lifecycle progress and meter newly completed rows."""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        completed_rows = serializer.validated_data.get('completed_rows')
        completed_rows_delta = max((completed_rows or 0) - instance.completed_rows, 0)
        with transaction.atomic():
            self.perform_update(serializer)
            if completed_rows_delta:
                # mapper.rows_per_project is already enforced, project-scoped and live,
                # against MapProject.rows_used at run creation (AutomatchRunListView.post)
                # - a run can't be declared past the cap in the first place, so completing
                # its already-approved rows needs no second gate here. Using
                # check_and_consume_capability here (a per-user, never-reset counter)
                # previously meant a user with multiple projects could get permanently
                # locked out of progress on ALL of them once their lifetime total crossed
                # the limit, even on a brand new project with zero rows. This only logs
                # the event for attribution/reporting.
                request.user.log_capability_usage(
                    MAPPER_ROWS_PER_PROJECT_CAPABILITY_ID, units=completed_rows_delta,
                    action='complete_automatch_rows', map_project=instance.map_project, run=instance
                )

        return Response(serializer.data)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
