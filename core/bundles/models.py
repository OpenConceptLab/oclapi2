from pydash import get

from core.bundles.constants import BUNDLE_TYPE_SEARCHSET, RESOURCE_TYPE
from core.collections.constants import SOURCE_MAPPINGS, SOURCE_TO_CONCEPTS
from core.common.constants import CASCADE_LEVELS_PARAM, CASCADE_MAPPINGS_PARAM, \
    CASCADE_HIERARCHY_PARAM, CASCADE_METHOD_PARAM, MAP_TYPES_PARAM, EXCLUDE_MAP_TYPES_PARAM, CASCADE_DIRECTION_PARAM, \
    INCLUDE_RETIRED_PARAM, RETURN_MAP_TYPES, ALL, OMIT_IF_EXISTS_IN, EQUIVALENCY_MAP_TYPES, HEAD, INCLUDE_SELF
from core.common.utils import get_truthy_values

TRUTHY = get_truthy_values()


class Bundle:
    def __init__(self, root, repo_version, params=None, verbose=False, requested_url=None):  # pylint: disable=too-many-arguments
        params = params or {}
        self.repo_version = repo_version
        self.params = params
        self.verbose = verbose
        self.listing = params.get('listing', '') in TRUTHY
        self.brief = params.get('brief', '') in TRUTHY or (not self.verbose and not self.listing)
        self.root = root
        self.reverse = False
        self.cascade_hierarchy = True
        self.cascade_mappings = True
        self.cascade_levels = ALL
        self.include_retired = False
        self.omit_if_exists_in = None
        self.include_self = True
        self.concepts = None
        self.mappings = None
        self._total = None
        self.concepts_count = 0
        self.mappings_count = 0
        self.cascade_method = SOURCE_TO_CONCEPTS
        self.map_types = None
        self.exclude_map_types = None
        self.return_map_types = None
        self.equivalency_map_types = None
        self.entries = []
        self.requested_url = requested_url
        self.repo_version_url = None
        self.set_repo_version_url()

    def set_cascade_parameters(self):
        self.set_cascade_direction()
        self.set_cascade_method()
        self.set_cascade_hierarchy()
        self.set_cascade_mappings()
        self.set_cascade_levels()
        self.set_include_retired()
        self.set_map_types()
        self.set_omit_if_exists_in()
        self.set_include_self()

    def set_repo_version_url(self):
        if self.repo_version:
            self.repo_version_url = self.repo_version.uri
            if self.repo_version.is_head:
                self.repo_version_url += HEAD + '/'

    @property
    def is_hierarchy_view(self):
        return self.params.get('view', '').lower() == 'hierarchy'

    def set_include_retired(self):
        if INCLUDE_RETIRED_PARAM in self.params:
            self.include_retired = self.params[INCLUDE_RETIRED_PARAM] in TRUTHY

    def set_map_types(self):
        self.map_types = self.params.get(MAP_TYPES_PARAM) or None
        self.exclude_map_types = self.params.get(EXCLUDE_MAP_TYPES_PARAM) or None
        self.return_map_types = self.params.get(RETURN_MAP_TYPES) or ALL
        self.equivalency_map_types = self.params.get(EQUIVALENCY_MAP_TYPES) or None

    def set_cascade_levels(self):
        if CASCADE_LEVELS_PARAM in self.params:
            level = self.params.get(CASCADE_LEVELS_PARAM, ALL)
            if level != ALL and level:
                self.cascade_levels = int(level)

    def set_cascade_mappings(self):
        if CASCADE_MAPPINGS_PARAM in self.params:
            self.cascade_mappings = self.params[CASCADE_MAPPINGS_PARAM] in TRUTHY

    def set_cascade_hierarchy(self):
        if CASCADE_HIERARCHY_PARAM in self.params:
            self.cascade_hierarchy = self.params[CASCADE_HIERARCHY_PARAM] in TRUTHY

    def set_cascade_method(self):
        if CASCADE_METHOD_PARAM in self.params:
            self.cascade_method = self.params.get(CASCADE_METHOD_PARAM, '').lower()

    def set_cascade_direction(self):
        if CASCADE_DIRECTION_PARAM in self.params:
            self.reverse = self.params[CASCADE_DIRECTION_PARAM] in TRUTHY

    def set_omit_if_exists_in(self):
        self.omit_if_exists_in = self.params.get(OMIT_IF_EXISTS_IN, None) or None

    def set_include_self(self):
        if INCLUDE_SELF in self.params:
            self.include_self = self.params[INCLUDE_SELF] in TRUTHY

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
        self.concepts_count = len(self.concepts) if isinstance(self.concepts, list) else self.concepts.count()

    def set_mappings_count(self):
        self.mappings_count = len(self.mappings) if isinstance(self.mappings, list) else self.mappings.count()

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
            map_types=self.map_types,
            exclude_map_types=self.exclude_map_types,
            cascade_mappings=self.cascade_mappings,
            cascade_hierarchy=self.cascade_hierarchy,
            cascade_levels=self.cascade_levels,
            include_retired=self.include_retired,
            reverse=self.reverse,
            return_map_types=self.return_map_types,
            omit_if_exists_in=self.omit_if_exists_in,
            equivalency_map_types=self.equivalency_map_types,
            include_self=self.include_self
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
            map_types=self.map_types,
            exclude_map_types=self.exclude_map_types,
            cascade_mappings=self.cascade_mappings,
            cascade_hierarchy=self.cascade_hierarchy,
            cascade_levels=self.cascade_levels,
            include_retired=self.include_retired,
            reverse=self.reverse,
            return_map_types=self.return_map_types,
            omit_if_exists_in=self.omit_if_exists_in,
            equivalency_map_types=self.equivalency_map_types
        )

        from core.concepts.serializers import ConceptMinimalSerializerRecursive
        self.entries = ConceptMinimalSerializerRecursive(
            self.root, context={'request': {'query_params': self.params}}).data

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
        serializer = Concept.get_serializer_class(verbose=self.verbose, version=True, brief=self.brief, cascade=True)
        return serializer

    @classmethod
    def clone(  # pylint: disable=too-many-arguments
            cls, concept_to_clone, clone_from_source, clone_to_source, user, requested_url,
            is_verbose=False, **parameters
    ):
        # if parameters:
        #     _parameters = parameters.copy()

        bundle = cls(
            root=None,
            params=parameters,
            verbose=is_verbose,
            repo_version=clone_from_source,
            requested_url=requested_url
        )
        bundle.set_cascade_parameters()
        _parameters = {}
        if parameters:
            _parameters = {
                'repo_version': clone_from_source,
                'map_types': bundle.map_types or '',
                'exclude_map_types': bundle.exclude_map_types,
                'cascade_mappings': bundle.cascade_mappings,
                'cascade_hierarchy': bundle.cascade_hierarchy,
                'cascade_levels': bundle.cascade_levels,
                'include_retired': bundle.include_retired,
                'reverse': bundle.reverse,
                'return_map_types': bundle.return_map_types,
                'equivalency_map_types': bundle.equivalency_map_types
            }
            if bundle.cascade_method:
                _parameters['source_mappings'] = bundle.cascade_method == SOURCE_MAPPINGS
                _parameters['source_to_concepts'] = bundle.cascade_method == SOURCE_TO_CONCEPTS

        added_concepts, added_mappings = clone_to_source.clone_with_cascade(concept_to_clone, user, **_parameters)
        bundle.root = clone_to_source.find_concept_by_mnemonic(concept_to_clone.mnemonic)
        bundle.concepts = added_concepts
        bundle.mappings = added_mappings
        bundle.set_total()
        bundle.set_entries()
        return bundle
