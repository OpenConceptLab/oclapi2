import tempfile
import zipfile
from wsgiref.util import FileWrapper

from rest_framework.renderers import JSONRenderer


class ZippedJSONRenderer(JSONRenderer):
    media_type = 'application/zip'
    format = 'zip'
    charset = None
    render_style = 'binary'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        ret = super().render(data, accepted_media_type, renderer_context)
        temp = tempfile.TemporaryFile()
        archive = zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED)
        archive.writestr('export.json', ret)
        archive.close()
        wrapper = FileWrapper(temp)
        temp.seek(0)
        return wrapper


class FhirRenderer(JSONRenderer):
    media_type = 'application/fhir+json'
