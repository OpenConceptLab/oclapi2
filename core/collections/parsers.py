from urllib import parse

from django.urls import resolve
from pydash import get

from core.collections.constants import CONCEPT_REFERENCE_TYPE, MAPPING_REFERENCE_TYPE
from core.collections.models import CollectionReference
from core.collections.utils import is_concept, is_mapping
from core.common.utils import to_parent_uri


class CollectionReferenceExpressionStringToStructuredParser:
    """
    1. This parser is specifically to convert old style reference syntax to new expanded reference syntax.
    2. This only works for OCL relative uris
    """
    def __init__(self, expression, cascade=None):  # expression may be a string or object
        self.expression = expression
        self.cascade = cascade
        self.is_unknown = False
        self.reference_type = None
        self.system = None
        self.version = None
        self.filter = None
        self.code = None
        self.valueset = None
        self.kwargs = None

    def is_source_expression(self):
        return '/sources/' in self.expression

    def is_collection_expression(self):
        return '/collections/' in self.expression

    def is_concept_expression(self):
        return is_concept(self.expression)

    def is_mapping_expression(self):
        return is_mapping(self.expression)

    def set_reference_type(self):
        if self.is_concept_expression():
            self.reference_type = CONCEPT_REFERENCE_TYPE
        elif self.is_mapping_expression():
            self.reference_type = MAPPING_REFERENCE_TYPE

    def set_system(self):
        if self.is_source_expression():
            self.set_version()
            self.system = to_parent_uri(self.expression)
            if self.version:
                self.system = self.system.replace(self.version, '').replace('//', '/')

    def set_valueset(self):
        if self.is_collection_expression():
            valueset = to_parent_uri(self.expression)
            version = get(self.kwargs, 'version')
            if version:
                valueset = valueset.replace(version, '').replace('//', '/')
                valueset += f"|{version}"
            self.valueset = [valueset]

    def set_filter(self):
        if '?' in self.expression:
            self.filter = []
            querystring = self.expression.split('?')[1]
            params = parse.parse_qs(querystring)
            for key, value in params.items():
                is_single = len(value) <= 1 if isinstance(value, list) else True
                self.filter.append(dict(
                    op=CollectionReference.OPERATOR_EQUAL if is_single else CollectionReference.OPERATOR_IN,
                    property=key,
                    value=','.join(value)
                ))

    def set_version(self):
        self.version = get(self.kwargs, 'version')

    def set_code(self):
        self.code = get(self.kwargs, 'concept') or get(self.kwargs, 'mapping')

    def check_unknown_expression(self):
        self.is_unknown = not self.kwargs

    def resolve_expression(self):
        try:
            self.kwargs = resolve(self.expression.split('?')[0]).kwargs
        except:
            self.kwargs = False

    def parse(self):
        self.resolve_expression()
        self.check_unknown_expression()
        self.set_reference_type()
        self.set_filter()
        if self.is_mapping_expression():
            self.cascade = None
        if not self.is_unknown:
            self.set_system()
            self.set_valueset()
            self.set_code()

    def to_reference_structure(self):
        return dict(
            expression=self.expression,
            reference_type=self.reference_type,
            cascade=self.cascade,
            system=self.system,
            version=self.version,
            code=self.code,
            valueset=self.valueset,
            filter=self.filter,
        )
