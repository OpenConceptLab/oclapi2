from django.db.models import Q
from pydash import compact, get

from core.bundles.constants import BUNDLE_TYPE_SEARCHSET, RESOURCE_TYPE
from core.collections.constants import SOURCE_MAPPINGS, SOURCE_TO_CONCEPTS
from core.common.constants import INCLUDE_MAPPINGS_PARAM, CASCADE_LEVELS_PARAM, CASCADE_MAPPINGS_PARAM, \
    CASCADE_HIERARCHY_PARAM, CASCADE_METHOD_PARAM, MAP_TYPES_PARAM, EXCLUDE_MAP_TYPES_PARAM, CASCADE_DIRECTION_PARAM


class Bundle:
    def __init__(self, root, repo_version, params=None, verbose=False):
        self.repo_version = repo_version
        self.params = params
        self.verbose = verbose
        self.brief = not self.verbose
        self.root = root
        self.reverse = False
        self.cascade_hierarchy = True
        self.cascade_mappings = True
        self.cascade_levels = '*'
        self.include_mappings = True
        self.concepts = None
        self.mappings = None
        self._total = None
        self.concepts_count = 0
        self.mappings_count = 0
        self.cascade_method = SOURCE_TO_CONCEPTS
        self.mappings_criteria = Q()
        self.entries = []

    def set_cascade_parameters(self):
        self.set_cascade_direction()
        self.set_cascade_method()
        self.set_cascade_hierarchy()
        self.set_cascade_mappings()
        self.set_cascade_mappings_criteria()
        self.set_cascade_levels()
        self.set_include_mappings()

    @property
    def is_hierarchy_view(self):
        return self.params.get('view', '').lower() == 'hierarchy'

    def set_include_mappings(self):
        if INCLUDE_MAPPINGS_PARAM in self.params:
            self.include_mappings = self.params[INCLUDE_MAPPINGS_PARAM] in ['true', True]

    def set_cascade_levels(self):
        if CASCADE_LEVELS_PARAM in self.params:
            level = self.params.get(CASCADE_LEVELS_PARAM, '*')
            if level != '*' and level:
                self.cascade_levels = int(level)

    def set_cascade_mappings(self):
        if CASCADE_MAPPINGS_PARAM in self.params:
            self.cascade_mappings = self.params[CASCADE_MAPPINGS_PARAM] in ['true', True]

    def set_cascade_hierarchy(self):
        if CASCADE_HIERARCHY_PARAM in self.params:
            self.cascade_hierarchy = self.params[CASCADE_HIERARCHY_PARAM] in ['true', True]

    def set_cascade_method(self):
        if CASCADE_METHOD_PARAM in self.params:
            self.cascade_method = self.params.get(CASCADE_METHOD_PARAM, '').lower()

    def set_cascade_direction(self):
        if CASCADE_DIRECTION_PARAM in self.params:
            self.reverse = self.params[CASCADE_DIRECTION_PARAM] in ['true', True]

    def set_cascade_mappings_criteria(self):
        map_types = self.params.dict().get(MAP_TYPES_PARAM, None)
        exclude_map_types = self.params.dict().get(EXCLUDE_MAP_TYPES_PARAM, None)
        if map_types:
            self.mappings_criteria &= Q(map_type__in=compact(map_types.split(',')))
        if exclude_map_types:
            self.mappings_criteria &= ~Q(map_type__in=compact(exclude_map_types.split(',')))

    @property
    def resource_type(self):
        return RESOURCE_TYPE

    @property
    def timestamp(self):  # pylint: disable=invalid-name
        return self.root.updated_at

    @property
    def bundle_type(self):
        return BUNDLE_TYPE_SEARCHSET

    @property
    def total(self):
        return self._total

    def set_concepts_count(self):
        self.concepts_count = self.concepts.count()

    def set_mappings_count(self):
        self.mappings_count = self.mappings.count()

    def set_total(self):
        self.set_concepts_count()
        self.set_mappings_count()
        self._total = self.concepts_count + self.mappings_count

    def cascade(self):
        if self.is_hierarchy_view:
            self.cascade_as_hierarchy()
        else:
            self.cascade_flat()

    def cascade_flat(self):
        self.set_cascade_parameters()
        result = self.root.cascade(
            repo_version=self.repo_version,
            source_mappings=self.cascade_method == SOURCE_MAPPINGS,
            source_to_concepts=self.cascade_method == SOURCE_TO_CONCEPTS,
            mappings_criteria=self.mappings_criteria,
            cascade_mappings=self.cascade_mappings,
            cascade_hierarchy=self.cascade_hierarchy,
            cascade_levels=self.cascade_levels,
            include_mappings=self.include_mappings,
            reverse=self.reverse
        )
        self.concepts = get(result, 'concepts')
        self.mappings = get(result, 'mappings')
        self.set_total()
        self.set_entries()

    def cascade_as_hierarchy(self):
        self.set_cascade_parameters()
        self.root.cascade_as_hierarchy(
            repo_version=self.repo_version,
            source_mappings=self.cascade_method == SOURCE_MAPPINGS,
            source_to_concepts=self.cascade_method == SOURCE_TO_CONCEPTS,
            mappings_criteria=self.mappings_criteria,
            cascade_mappings=self.cascade_mappings,
            cascade_hierarchy=self.cascade_hierarchy,
            cascade_levels=self.cascade_levels,
            include_mappings=self.include_mappings,
            reverse=self.reverse
        )

        from core.concepts.serializers import ConceptMinimalSerializerRecursive
        self.entries = ConceptMinimalSerializerRecursive(
            self.root, context=dict(request=dict(query_params=self.params))).data

    def set_entries(self):
        self.entries += self.get_concept_serializer()(self.concepts, many=True).data
        self.entries += self.get_mapping_serializer()(self.mappings, many=True).data

    def get_mapping_serializer(self):
        from core.mappings.models import Mapping
        serializer = Mapping.get_serializer_class(
            verbose=self.verbose, version=True, brief=self.brief, reverse=self.reverse)
        return serializer

    def get_concept_serializer(self):
        from core.concepts.models import Concept
        serializer = Concept.get_serializer_class(verbose=self.verbose, version=True, brief=self.brief)
        return serializer
