from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import RetrieveUpdateDestroyAPIView, RetrieveAPIView, CreateAPIView, \
    RetrieveUpdateAPIView
from rest_framework.response import Response

from core.common.mixins import ListWithHeadersMixin, ConceptDictionaryCreateMixin
from core.common.permissions import HasOwnership, CanEditConceptDictionary
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
        permission = HasOwnership()
        if not permission.has_object_permission(request, self, self.parent_resource):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(data=MapProject.format_request_data(request.data, self.parent_resource))
        if serializer.is_valid():
            instance = serializer.save(force_insert=True)
            if serializer.is_valid():
                headers = self.get_success_headers(serializer.data)
                serializer = self.get_serializer(instance)
                return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


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
        if self.request.method == 'POST':
            context['map_project'] = self.get_map_project()
        return context

    def get_queryset(self):
        return self.get_map_project().auto_match_runs.select_related('started_by').all()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
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

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
