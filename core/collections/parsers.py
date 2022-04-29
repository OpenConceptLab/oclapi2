from urllib import parse

from django.urls import resolve
from pydash import get, compact, flatten

from core.collections.constants import CONCEPT_REFERENCE_TYPE, MAPPING_REFERENCE_TYPE, ALL_SYMBOL
from core.collections.models import CollectionReference
from core.collections.utils import is_concept, is_mapping
from core.common.utils import to_parent_uri


class CollectionReferenceAbstractParser:
    def __init__(self, expression, cascade=None):
        self.expression = expression
        self.cascade = cascade
        self.references = []
        self.parsers = []
        self.instances = []

    def parse(self):
        pass

    def to_reference_structure(self):
        for parser in self.parsers:
            references = parser.to_reference_structure()
            self.references += references
        self.references = compact(flatten(self.references))
        return self.references

    def to_objects(self):
        for reference in self.references:
            self.instances.append(CollectionReference(**reference))
        return self.instances


class CollectionReferenceParser(CollectionReferenceAbstractParser):
    def is_old_style_expression(self):
        return isinstance(self.expression, str) or (
                isinstance(self.expression, dict) and (
                    'uri' in self.expression or
                    'concepts' in self.expression or
                    'mappings' in self.expression or
                    'expressions' in self.expression)
            )

    def parse(self):
        if self.is_old_style_expression():
            self.parsers.append(CollectionReferenceOldStyleToExpandedStructureParser(self.expression, self.cascade))
        else:
            self.parsers.append(CollectionReferenceExpandedStructureParser(self.expression, self.cascade))
        for parser in self.parsers:
            parser.parse()


class CollectionReferenceExpandedStructureParser(CollectionReferenceAbstractParser):
    """ New style parser """

    def parse(self):
        if isinstance(self.expression, dict):
            self.references.append(self.to_reference_structure())
        elif isinstance(self.expression, list):
            for expression in self.expression:
                self.references.append(self.to_reference_structure(expression))

    def to_reference_structure(self, expression=None):
        expression = expression or self.expression
        self.references = [dict(
            expression=None,
            system=get(expression, 'system'),
            version=get(expression, 'version'),
            reference_type=get(expression, 'reference_type', 'concepts'),
            valueset=get(expression, 'valueset'),
            cascade=get(expression, 'cascade') or self.cascade,
            filter=get(expression, 'filter'),
            code=get(expression, 'code'),
        )]
        return self.references


class CollectionReferenceOldStyleToExpandedStructureParser(CollectionReferenceAbstractParser):
    def parse(self):
        if isinstance(self.expression, dict):
            if self.expression.get('uri'):
                self.parsers.append(CollectionReferenceSourceAllExpressionParser(self.expression))

            for attr in ['concepts', 'mappings', 'expressions']:
                if self.expression.get(attr) and isinstance(self.expression.get(attr), list):
                    for expression in self.expression[attr]:
                        self.parsers.append(CollectionReferenceExpressionStringParser(expression, self.cascade))
        elif isinstance(self.expression, list):
            for expression in self.expression:
                if isinstance(expression, str):
                    self.parsers.append(CollectionReferenceExpressionStringParser(expression, self.cascade))
        elif isinstance(self.expression, str):
            self.parsers.append(CollectionReferenceExpressionStringParser(self.expression, self.cascade))
        for parser in self.parsers:
            parser.parse()


class CollectionReferenceSourceAllExpressionParser(CollectionReferenceAbstractParser):
    """
    1. This parser is specifically to convert old style source concepts/mappings all expression to expanded syntax
    2. This only works for OCL relative urls
    3. Accepts expression in the form of:
        {
            "uri": "/orgs/MyOrg/sources/MySource/<?version>/",
            "concepts": "*",
            "mappings": "*"
        }
    """

    def __init__(self, expression, cascade=None):
        super().__init__(expression, cascade)
        self.expression_str = None

    def set_expression_string(self):
        self.expression_str = self.expression.get('uri', '')

    def parse(self):
        self.set_expression_string()

    def to_reference_structure(self):
        if self.expression.get('concepts') == ALL_SYMBOL:
            parser = CollectionReferenceExpressionStringParser(self.expression_str + 'concepts/')
            parser.parse()
            self.references.append(parser.to_reference_structure())
        if self.expression.get('mappings') == ALL_SYMBOL:
            parser = CollectionReferenceExpressionStringParser(self.expression_str + 'mappings/')
            parser.parse()
            self.references.append(parser.to_reference_structure())

        return flatten(self.references)


class CollectionReferenceExpressionStringParser(CollectionReferenceAbstractParser):
    """
    1. This parser is specifically to convert old style reference syntax to new expanded reference syntax.
    2. This only works for OCL relative uris
    3. Accepts only string expressions
    """

    def __init__(self, expression, cascade=None):
        super().__init__(expression, cascade)
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
        self.references = [dict(
            expression=self.expression,
            reference_type=self.reference_type,
            cascade=self.cascade,
            system=self.system,
            version=self.version,
            code=self.code,
            valueset=self.valueset,
            filter=self.filter,
        )]
        return self.references
