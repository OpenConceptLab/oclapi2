from django.db.models import Q
from pydash import compact, get

from core.bundles.constants import BUNDLE_TYPE
from core.collections.constants import SOURCE_MAPPINGS, SOURCE_TO_CONCEPTS


class BundleMeta:
    def __init__(self, root):
        self.root = root

    @property
    def last_updated(self):
        return self.root.updated_at

    def to_dict(self):
        return dict(lastUpdated=self.last_updated)


class Bundle:
    def __init__(self, root, params=None, verbose=False):
        self.verbose = verbose
        self.brief = not verbose
        self.root = root
        self.params = params
        self._meta = BundleMeta(self.root)
        self.concepts = None
        self.mappings = None
        self._total = None
        self.cascade_method = None
        self.mapping_criteria = Q()
        self.entries = []

    def set_cascade_method(self):
        self.cascade_method = self.params.get('method', '').lower()

    def set_cascade_mapping_filters(self):
        map_types = self.params.dict().get('mapTypes', None)
        exclude_map_types = self.params.dict().get('excludeMapTypes', None)
        if map_types:
            self.mapping_criteria &= Q(map_type__in=compact(map_types.split(',')))
        if exclude_map_types:
            self.mapping_criteria &= ~Q(map_type__in=compact(exclude_map_types.split(',')))

    @property
    def resource_type(self):
        return BUNDLE_TYPE

    @property
    def id(self):  # pylint: disable=invalid-name
        return self.root.mnemonic

    @property
    def timestamp(self):  # pylint: disable=invalid-name
        return self.root.updated_at

    @property
    def meta(self):
        return self._meta.to_dict()

    @property
    def bundle_type(self):
        return self.root.resource_type

    @property
    def total(self):
        if self._total:
            return self._total
        self.set_total()
        return self._total

    def set_total(self):
        total = self.concepts.count()
        total += self.mappings.count()

        self._total = total

    def cascade(self):
        self.set_cascade_method()
        self.set_cascade_mapping_filters()
        result = self.root.get_cascaded_resources(
            source_mappings=self.cascade_method == SOURCE_MAPPINGS,
            source_to_concepts=self.cascade_method == SOURCE_TO_CONCEPTS,
            mapping_criteria=self.mapping_criteria
        )
        self.concepts = get(result, 'concepts')
        self.mappings = get(result, 'mappings')
        self.set_total()
        self.set_entries()

    @property
    def entry(self):
        if self.entries:
            return self.entries
        self.set_entries()
        return self.entries

    def set_entries(self):
        from core.concepts.models import Concept
        serializer = Concept.get_serializer_class(verbose=self.verbose, version=True, brief=self.brief)
        self.entries += serializer(self.concepts, many=True).data
        from core.mappings.models import Mapping
        serializer = Mapping.get_serializer_class(verbose=self.verbose, version=True, brief=self.brief)
        self.entries += serializer(self.mappings, many=True).data
