from urllib import parse

from django.urls import resolve
from pydash import get, compact, flatten

from core.collections.constants import CONCEPT_REFERENCE_TYPE, MAPPING_REFERENCE_TYPE, ALL_SYMBOL, SOURCE_TO_CONCEPTS, \
    SOURCE_MAPPINGS
from core.collections.utils import is_concept, is_mapping
from core.common.utils import to_parent_uri, drop_version


class CollectionReferenceAbstractParser:
    def __init__(self, expression, transform=None, cascade=None, user=None):
        self.expression = expression
        self.transform = transform
        self.cascade = cascade
        self.user = user
        self.references = []
        self.parsers = []
        self.instances = []

    def parse(self):
        pass

    @staticmethod
    def get_include_value(expression):
        include = True
        if get(expression, 'exclude') and expression['exclude']:
            include = False
        if get(expression, 'include'):
            include = bool(expression['include'])
        return include

    @staticmethod
    def get_formatted_valueset(valueset):
        if valueset:
            if isinstance(valueset, str):
                return [valueset]
            return valueset
        return None

    def to_reference_structure(self):
        self.references = []
        for parser in self.parsers:
            self.references += parser.to_reference_structure()
        self.references = compact(flatten(self.references))
        return self.references

    def to_objects(self):
        from core.collections.models import CollectionReference
        for reference in self.references:
            reference['valueset'] = self.get_formatted_valueset(reference.get('valueset'))
            self.instances.append(CollectionReference(**reference))
        return self.instances


class CollectionReferenceParser(CollectionReferenceAbstractParser):
    def is_old_style_expression(self):
        return isinstance(self.expression, str) or (
                isinstance(self.expression, dict) and (
                    'system' not in self.expression and 'valueset' not in self.expression
                ) and (
                    'uri' in self.expression or
                    'concepts' in self.expression or
                    'mappings' in self.expression or
                    'expressions' in self.expression)
            )

    def parse(self):
        if self.is_old_style_expression():
            self.parsers.append(
                CollectionReferenceOldStyleToExpandedStructureParser(
                    self.expression, self.transform, self.cascade, self.user))
        else:
            self.parsers.append(
                CollectionReferenceExpandedStructureParser(
                    self.expression, self.transform, self.cascade, self.user))
        for parser in self.parsers:
            parser.parse()

    def to_objects(self):
        from core.collections.models import CollectionReference
        cascade_to_concepts = self.cascade == SOURCE_TO_CONCEPTS
        cascade_mappings = self.cascade == SOURCE_MAPPINGS
        should_cascade_now = cascade_mappings or cascade_to_concepts
        for reference in self.references:
            reference['valueset'] = self.get_formatted_valueset(reference.get('valueset'))
            collection_reference = CollectionReference(**reference)
            if should_cascade_now and collection_reference.code:
                uris = collection_reference.get_related_uris()
                for uri in uris:
                    existing_uris = [collection_reference.expression, drop_version(collection_reference.expression)]
                    if uri not in existing_uris and drop_version(uri) not in existing_uris:
                        parser = CollectionReferenceExpressionStringParser(uri, self.transform)
                        parser.parse()
                        _reference = parser.to_reference_structure()[0]
                        _reference['valueset'] = self.get_formatted_valueset(_reference.get('valueset'))
                        self.instances.append(CollectionReference(**_reference))
            self.instances.append(collection_reference)
        return self.instances


class CollectionReferenceExpandedStructureParser(CollectionReferenceAbstractParser):
    """ New style parser """

    def to_reference_structure(self):
        self.references = []
        if isinstance(self.expression, dict):
            self._to_reference_structure()
        elif isinstance(self.expression, list):
            for expression in self.expression:
                self._to_reference_structure(expression)
        return self.references

    def _to_reference_structure(self, expression=None):  # pylint: disable=arguments-differ
        expression = expression or self.expression
        if isinstance(get(expression, 'url'), list):
            return self.references
        concept = expression.get('concept', None)
        mapping = expression.get('mapping', None)
        if concept:
            if not isinstance(concept, list):
                concept = [concept]
            for _concept in concept:
                code = _concept if isinstance(_concept, str) else _concept.get('code', None)
                display = None if isinstance(_concept, str) else _concept.get('display', None)
                resource_version = None if isinstance(_concept, str) else _concept.get('resource_version', None)
                self.references.append(
                    dict(
                        expression=None,
                        namespace=get(expression, 'namespace'),
                        system=get(expression, 'system') or get(expression, 'url'),
                        version=get(expression, 'version'),
                        reference_type='concepts',
                        valueset=get(expression, 'valueset') or get(expression, 'valueSet'),
                        cascade=get(expression, 'cascade') or self.cascade,
                        filter=get(expression, 'filter'),
                        code=code,
                        resource_version=resource_version,
                        transform=get(expression, 'transform'),
                        created_by=self.user,
                        display=display,
                        include=self.get_include_value(expression)
                    )
                )
        if mapping:
            if not isinstance(mapping, list):
                mapping = [mapping]
            for _mapping in mapping:
                code = _mapping if isinstance(_mapping, str) else _mapping.get('code', None)
                resource_version = None if isinstance(_mapping, str) else _mapping.get('resource_version', None)
                self.references.append(
                    dict(
                        expression=None,
                        namespace=get(expression, 'namespace'),
                        system=get(expression, 'system') or get(expression, 'url'),
                        version=get(expression, 'version'),
                        reference_type='mappings',
                        valueset=get(expression, 'valueset') or get(expression, 'valueSet'),
                        cascade=get(expression, 'cascade') or self.cascade,
                        filter=get(expression, 'filter'),
                        code=code,
                        resource_version=resource_version,
                        transform=get(expression, 'transform'),
                        created_by=self.user,
                        display=None,
                        include=self.get_include_value(expression)
                    )
                )
        if not concept and not mapping:
            self.references.append(dict(
                expression=None,
                namespace=get(expression, 'namespace'),
                system=get(expression, 'system') or get(expression, 'url'),
                version=get(expression, 'version'),
                reference_type=get(expression, 'reference_type', 'concepts'),
                valueset=get(expression, 'valueset') or get(expression, 'valueSet'),
                cascade=get(expression, 'cascade') or self.cascade,
                filter=get(expression, 'filter'),
                code=get(expression, 'code'),
                resource_version=get(expression, 'resource_version'),
                transform=get(expression, 'transform'),
                created_by=self.user,
                display=get(expression, 'display'),
                include=self.get_include_value(expression)
            ))
        return self.references


class CollectionReferenceOldStyleToExpandedStructureParser(CollectionReferenceAbstractParser):
    def parse(self):
        if isinstance(self.expression, dict):
            if self.expression.get('uri'):
                self.parsers.append(CollectionReferenceSourceAllExpressionParser(self.expression))

            for attr in ['concepts', 'mappings', 'expressions']:
                if self.expression.get(attr) and isinstance(self.expression.get(attr), list):
                    for expression in self.expression[attr]:
                        self.parsers.append(CollectionReferenceExpressionStringParser(
                            expression, self.transform, self.cascade, self.user))
        elif isinstance(self.expression, list):
            for expression in self.expression:
                if isinstance(expression, str):
                    self.parsers.append(
                        CollectionReferenceExpressionStringParser(expression, self.transform, self.cascade, self.user))
        elif isinstance(self.expression, str):
            self.parsers.append(
                CollectionReferenceExpressionStringParser(self.expression, self.transform, self.cascade, self.user))
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

    def __init__(self, expression, transform=None, cascade=None, user=None):
        super().__init__(expression, transform, cascade, user)
        self.expression_str = None

    def set_expression_string(self):
        self.expression_str = self.expression.get('uri', '')

    def parse(self):
        self.set_expression_string()
        if self.expression.get('concepts') == ALL_SYMBOL:
            self.parsers.append(CollectionReferenceExpressionStringParser(
                self.expression_str + 'concepts/', self.transform, self.cascade, self.user))
        if self.expression.get('mappings') == ALL_SYMBOL:
            self.parsers.append(CollectionReferenceExpressionStringParser(
                self.expression_str + 'mappings/', self.transform, self.cascade, self.user))
        for parser in self.parsers:
            parser.parse()


class CollectionReferenceExpressionStringParser(CollectionReferenceAbstractParser):
    """
    1. This parser is specifically to convert old style reference syntax to new expanded reference syntax.
    2. This only works for OCL relative uris
    3. Accepts only string expressions
    """

    def __init__(self, expression, transform=None, cascade=None, user=None):
        super().__init__(expression, transform, cascade, user)
        self.is_unknown = False
        self.reference_type = None
        self.system = None
        self.version = None
        self.filter = None
        self.code = None
        self.resource_version = None
        self.valueset = None
        self.display = None
        self.include = True
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
            from core.collections.models import CollectionReference
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

    def set_resource_version(self):
        self.resource_version = get(self.kwargs, 'concept_version') or get(self.kwargs, 'mapping_version')

    def check_unknown_expression(self):
        self.is_unknown = not self.kwargs

    def resolve_expression(self):
        try:
            self.kwargs = resolve(self.expression.split('?')[0]).kwargs
        except:  # pylint: disable=bare-except
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
            self.set_resource_version()

    def to_reference_structure(self):
        self.references.append(dict(
            expression=self.expression,
            reference_type=self.reference_type,
            cascade=self.cascade,
            system=self.system,
            version=self.version,
            code=self.code,
            resource_version=self.resource_version,
            valueset=self.valueset,
            filter=self.filter,
            transform=self.transform,
            created_by=self.user,
            display=self.display,
            include=self.include
        ))
        return self.references
