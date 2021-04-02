from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.tasks import import_v1_content


class BaseImporterView(APIView):
    importer = None
    swagger_schema = None
    permission_classes = (IsAdminUser,)

    def post(self, request):
        file_url = request.data.get('file_url', None)
        drop_version_if_version_missing = request.data.get(
            'drop_version_if_version_missing', None) in [True, 'true', 'True']
        if not file_url:
            return Response(dict(error=['Export File URL is mandatory']), status=status.HTTP_400_BAD_REQUEST)

        task = import_v1_content.apply_async((self.importer, file_url, drop_version_if_version_missing, ), )
        return Response(dict(task=task.id, state=task.state, queue=task.queue), status=status.HTTP_202_ACCEPTED)


class OrganizationsImporterView(BaseImporterView):
    importer = 'organization'


class UsersImporterView(BaseImporterView):
    importer = 'user'


class SourcesImporterView(BaseImporterView):
    importer = 'source'


class SourceVersionsImporterView(BaseImporterView):
    importer = 'source_version'


class SourceIdsImporterView(BaseImporterView):
    importer = 'source_id'


class CollectionsImporterView(BaseImporterView):
    importer = 'collection'


class CollectionVersionsImporterView(BaseImporterView):
    importer = 'collection_version'


class CollectionIdsImporterView(BaseImporterView):
    importer = 'collection_id'


class ConceptsImporterView(BaseImporterView):
    importer = 'concept'


class ConceptVersionsImporterView(BaseImporterView):
    importer = 'concept_version'


class ConceptIdsImporterView(BaseImporterView):
    importer = 'concept_id'


class MappingsImporterView(BaseImporterView):
    importer = 'mapping'


class MappingVersionsImporterView(BaseImporterView):
    importer = 'mapping_version'


class WebUserCredentialsImporterView(BaseImporterView):
    importer = 'web_user_credential'


class CollectionReferenceImporterView(BaseImporterView):
    importer = 'collection_reference'
