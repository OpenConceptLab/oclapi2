from fhir.resources.codesystem import CodeSystem

from core.settings import DEFAULT_LOCALE
from core.sources.models import Source


class CodeSystemConverter:
    """It is intended as a replacement for serializers"""

    @staticmethod
    def can_convert_from_fhir(obj):
        return isinstance(obj, CodeSystem) or (isinstance(obj, dict) and obj.get('resourceType') == 'CodeSystem')

    @staticmethod
    def can_convert_to_fhir(obj):
        return isinstance(obj, Source) or (isinstance(obj, dict) and obj.get('type') == 'Source')

    @staticmethod
    def to_fhir(source):
        if isinstance(source, dict):
            res = source
            res_type = res.pop('type')
            source = Source(**res)
            res.update({'type', res_type})

        code_system = {
            'id': source.mnemonic,
            'status': 'retired' if source.retired else 'active' if source.released else 'draft',
            'content': source.content_type if source.content_type else 'complete',
            'url': source.canonical_url,
            'title': source.name,
            'language': source.default_locale,
            'count': source.active_concepts,
        }
        return CodeSystem(**code_system).dict()

    @staticmethod
    def from_fhir(code_system):
        if isinstance(code_system, dict):
            code_system = CodeSystem(**code_system)

        source = {
            'type': 'Source',
            'mnemonic': code_system.id,
            'canonical_url': code_system.url,
            'name': code_system.title,
            'default_locale': code_system.language if code_system.language else DEFAULT_LOCALE,
            'content_type': code_system.content,
            'retired': code_system.status == 'retired',
            'released': code_system.status == 'active',
        }
        return source
