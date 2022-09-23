from pydash import get

from core.collections.constants import SOURCE_TO_CONCEPTS, SOURCE_MAPPINGS
from core.common.utils import is_url_encoded_string, decode_string


class CollectionReferenceTranslator:
    def __init__(self, reference):
        self.reference = reference

    @staticmethod
    def __format_system_value(value):
        if value.startswith('http'):
            return value
        return value.replace(
            '/users/', '').replace('/orgs/', '').replace('/sources/', '/').replace('/collections/', '/').strip("/")

    def __has_any_repo_version(self):
        return bool(
            self.reference.version or (
                    isinstance(self.reference.valueset, list) and any(
                        bool(get(valueset.split('|'), '1')) for valueset in self.reference.valueset)
            )
        )

    @property
    def ref_entity(self):
        return 'concept' if self.reference.is_concept else 'mapping'

    @property
    def reference_effect(self):
        return 'Include' if self.reference.include else 'Exclude'

    def __get_cascade_translation(self):
        english = ''
        cascade = None
        if self.reference.cascade:
            if isinstance(self.reference.cascade, str):
                cascade = self.reference.cascade
            elif isinstance(self.reference.cascade, dict):
                cascade = self.reference.cascade.get('method')
            if cascade:
                cascade = cascade.lower()
                if cascade == SOURCE_TO_CONCEPTS:
                    english += 'PLUS its mappings and their target concepts '
                if cascade == SOURCE_MAPPINGS:
                    english += 'PLUS its mappings '
        return english

    def translate(self):  # pylint: disable=too-many-branches,too-many-statements
        english = f'{self.reference_effect} '
        if not self.__has_any_repo_version() and not self.reference.resource_version:
            english += 'latest '
        entity = self.ref_entity
        code = self.reference.code
        if code:
            code = decode_string(decode_string(code)) if is_url_encoded_string(code) else code
            if self.reference.resource_version:
                english += f'version "{self.reference.resource_version}" of '
            elif self.reference.transform:
                english += 'latest version of '
            english += f'{entity} "{code}" from '
        else:
            english += f'{entity}s '
            if self.reference.system or self.reference.valueset:
                english += 'from '
        if self.reference.system:
            if self.reference.version:
                english += f'version "{self.reference.version}" of '
            english += f'{self.__format_system_value(self.reference.system)} '
        if self.reference.valueset and isinstance(self.reference.valueset, list):
            if self.reference.system:
                english += 'intersection with '
            __count = 0
            for valueset in self.reference.valueset:
                if __count > 0:
                    english += 'intersection with '
                parts = valueset.split('|')
                collection = self.__format_system_value(parts[0])
                coll_version = get(parts, '1') or None
                if coll_version:
                    english += f'version "{coll_version}" of '
                english += f'{collection} '
                __count += 1
        if self.reference.filter:
            __count = 0
            for filter_def in self.reference.filter:
                if filter_def['property'] == 'q' and filter_def['value']:
                    if __count > 0:
                        english += '& '
                    english += f'containing "{filter_def["value"]}" '
                    __count += 1
                elif filter_def['property'] == 'exact_match' and filter_def['value']:
                    if __count > 0:
                        english += '& '
                    english += f'matching exactly with "{filter_def["value"]}" '
                    __count += 1
                elif filter_def['value']:
                    if __count > 0:
                        english += '& '
                    english += f'having {filter_def["property"]} equal to "{filter_def["value"]}" '
                    __count += 1
        english += self.__get_cascade_translation()
        return english.strip()
