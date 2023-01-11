from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, HashIndex
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError, transaction, connection
from django.db.models import F, Q
from pydash import get, compact

from core.common.constants import ISO_639_1, LATEST, HEAD, ALL
from core.common.mixins import SourceChildMixin
from core.common.models import VersionedModel, ConceptContainerModel
from core.common.tasks import process_hierarchy_for_new_concept, process_hierarchy_for_concept_version, \
    process_hierarchy_for_new_parent_concept_version
from core.common.utils import generate_temp_version, drop_version, \
    encode_string, decode_string, named_tuple_fetchall, startswith_temp_version, is_versioned_uri
from core.concepts.constants import CONCEPT_TYPE, LOCALES_FULLY_SPECIFIED, LOCALES_SHORT, LOCALES_SEARCH_INDEX_TERM, \
    CONCEPT_WAS_RETIRED, CONCEPT_IS_ALREADY_RETIRED, CONCEPT_IS_ALREADY_NOT_RETIRED, CONCEPT_WAS_UNRETIRED, \
    PERSIST_CLONE_ERROR, PERSIST_CLONE_SPECIFY_USER_ERROR, ALREADY_EXISTS, CONCEPT_REGEX, MAX_LOCALES_LIMIT, \
    MAX_NAMES_LIMIT, MAX_DESCRIPTIONS_LIMIT
from core.concepts.mixins import ConceptValidationMixin


class AbstractLocalizedText(models.Model):
    class Meta:
        abstract = True
        indexes = [
            HashIndex(fields=['name']),
            models.Index(fields=['type']),
            models.Index(fields=['locale']),
            models.Index(fields=['locale_preferred']),
            models.Index(fields=['created_at']),
        ]

    id = models.BigAutoField(primary_key=True)
    external_id = models.TextField(null=True, blank=True)
    name = models.TextField()
    type = models.TextField(null=True, blank=True)
    locale = models.TextField()
    locale_preferred = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def to_dict(self):
        return dict(
            external_id=self.external_id, name=self.name, type=self.type, locale=self.locale,
            locale_preferred=self.locale_preferred,
        )

    def clone(self):
        return self.__class__(
            external_id=self.external_id,
            name=self.name,
            type=self.type,
            locale=self.locale,
            locale_preferred=self.locale_preferred
        )

    @staticmethod
    def _build(_):
        pass

    @classmethod
    def build(cls, params):
        if not params:
            return []
        if isinstance(params, list):
            return [cls._build(param) for param in params]
        return cls._build(params)

    @property
    def is_fully_specified(self):
        return self.type in LOCALES_FULLY_SPECIFIED or self.is_fully_specified_after_clean

    @property
    def is_fully_specified_after_clean(self):  # needed for OpenMRS schema content created from TermBrowser
        if not self.type:
            return False
        _type = self.type.replace(' ', '').replace('-', '').replace('_', '').lower()
        return _type == 'fullyspecified'

    @property
    def is_short(self):
        return self.type in LOCALES_SHORT

    @property
    def is_search_index_term(self):
        return self.type in LOCALES_SEARCH_INDEX_TERM


class ConceptDescription(AbstractLocalizedText):
    concept = models.ForeignKey('concepts.Concept', on_delete=models.CASCADE, related_name='descriptions')

    class Meta:
        db_table = 'concept_descriptions'
        indexes = AbstractLocalizedText.Meta.indexes

    @staticmethod
    def _build(params):
        _type = params.get('type', None)
        description_type = params.get('description_type', None)
        if not description_type or description_type == 'ConceptDescription':
            description_type = _type

        description_name = params.get('description', None) or params.get('name', None)
        return ConceptDescription(
            **{
                **{k: v for k, v in params.items() if k not in ['description', 'name', 'type', 'description_type']},
                'type': description_type,
                'name': description_name,
            }
        )


class ConceptName(AbstractLocalizedText):
    concept = models.ForeignKey(
        'concepts.Concept', on_delete=models.CASCADE, null=True, blank=True, related_name='names')

    class Meta:
        db_table = 'concept_names'
        indexes = AbstractLocalizedText.Meta.indexes

    @staticmethod
    def _build(params):
        _type = params.get('type', None)
        name_type = params.get('name_type', None)
        if not name_type or name_type == 'ConceptName':
            name_type = _type

        return ConceptName(
            **{
                **{k: v for k, v in params.items() if k not in ['type', 'name_type']},
                'type': name_type
            }
        )

    @classmethod
    def get_dormant_queryset(cls):
        return cls.objects.filter(name_locales__isnull=True, description_locales__isnull=True)

    @classmethod
    def dormants(cls, raw=True):
        if raw:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT("concept_names"."id") FROM "concept_names"
                    WHERE NOT EXISTS (SELECT 1 FROM "concepts_names" WHERE
                    "concepts_names"."localizedtext_id" = "concept_names"."id")
                    AND NOT EXISTS (SELECT 1 FROM "concepts_descriptions"
                    WHERE "concepts_descriptions"."localizedtext_id" = "concept_names"."id")
                    """
                )
                count, = cursor.fetchone()
                return count

        return cls.get_dormant_queryset().count()


class HierarchicalConcepts(models.Model):
    child = models.ForeignKey('concepts.Concept', related_name='child_parent', on_delete=models.CASCADE)
    parent = models.ForeignKey('concepts.Concept', related_name='parent_child', on_delete=models.CASCADE)


class Concept(ConceptValidationMixin, SourceChildMixin, VersionedModel):  # pylint: disable=too-many-public-methods
    class Meta:
        db_table = 'concepts'
        unique_together = ('mnemonic', 'version', 'parent')
        indexes = [
            models.Index(name='concepts_updated_6490d8_idx', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False) & ~Q(public_access='None'))),
            models.Index(name='concepts_ver_sort_idx', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True) &
                                    ~Q(public_access='None'))),
            models.Index(name='concepts_public_conditional', fields=['public_access'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True) &
                                    ~Q(public_access='None'))),
            models.Index(name='concepts_ver_public', fields=['public_access'],
                         condition=(Q(is_active=True) & Q(retired=False) & ~Q(public_access='None'))),
            models.Index(name='concepts_public_cond', fields=['parent_id'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True) &
                                    ~Q(public_access='None'))),
            models.Index(name='concepts_all_for_count', fields=['is_active'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True))),
            models.Index(name='concepts_ver_for_count', fields=['is_active'],
                         condition=(Q(is_active=True) & Q(retired=False))),
            models.Index(name='concepts_all_for_count2', fields=['parent_id'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True))),
            models.Index(name='concepts_all_for_sort', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True))),
            models.Index(name='concepts_ver_for_sort', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False))),
            models.Index(name='concepts_ver_updated_idx', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')) &
                                    ~Q(public_access='None'))),
            models.Index(name='concepts_ver_public_cond', fields=['public_access'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')) &
                                    ~Q(public_access='None'))),
            models.Index(name='concepts_ver_public_cond2', fields=['parent_id'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')) &
                                    ~Q(public_access='None'))),
            models.Index(name='concepts_ver_all_for_count', fields=['is_active'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')))),
            models.Index(name='concepts_ver_all_for_count2', fields=['parent_id'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')))),
            models.Index(name='concepts_ver_all_for_sort', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(id=F('versioned_object_id')))),
            models.Index(name='concepts_ver_all_for_sort_2', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False))),
            GinIndex(
                name='concepts_uri_trgm_id_gin_idx',
                fields=['uri', 'id'],
                opclasses=['gin_trgm_ops', 'int8_ops'],
                condition=Q(is_latest_version=True)
            )
        ] + VersionedModel.Meta.indexes

    external_id = models.TextField(null=True, blank=True)
    concept_class = models.TextField()
    datatype = models.TextField()
    names_old = models.ManyToManyField(ConceptName, related_name='name_locales')
    descriptions_old = models.ManyToManyField(ConceptName, related_name='description_locales')
    comment = models.TextField(null=True, blank=True)
    parent = models.ForeignKey('sources.Source', related_name='concepts_set', on_delete=models.CASCADE)
    sources = models.ManyToManyField('sources.Source', related_name='concepts')
    versioned_object = models.ForeignKey(
        'self', related_name='versions_set', null=True, blank=True, on_delete=models.CASCADE
    )
    parent_concepts = models.ManyToManyField(
        'self', through='HierarchicalConcepts', symmetrical=False, related_name='child_concepts'
    )
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=CONCEPT_REGEX)],
        db_index=True
    )
    _counted = models.BooleanField(default=True, null=True, blank=True)
    _index = models.BooleanField(default=True)
    logo_path = None

    OBJECT_TYPE = CONCEPT_TYPE
    ALREADY_RETIRED = CONCEPT_IS_ALREADY_RETIRED
    ALREADY_NOT_RETIRED = CONCEPT_IS_ALREADY_NOT_RETIRED
    WAS_RETIRED = CONCEPT_WAS_RETIRED
    WAS_UNRETIRED = CONCEPT_WAS_UNRETIRED

    # $cascade as hierarchy attributes
    cascaded_entries = None
    terminal = None

    es_fields = {
        'id': {'sortable': True, 'filterable': True, 'exact': True},
        'numeric_id': {'sortable': True, 'filterable': False, 'exact': False},
        'name': {'sortable': False, 'filterable': True, 'exact': True},
        '_name': {'sortable': True, 'filterable': False, 'exact': False},
        'last_update': {'sortable': True, 'filterable': False, 'default': 'desc'},
        'is_latest_version': {'sortable': False, 'filterable': True},
        'concept_class': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'datatype': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'locale': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'synonyms': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'retired': {'sortable': False, 'filterable': True, 'facet': True},
        'source': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'collection': {'sortable': False, 'filterable': True, 'facet': True},
        'collection_url': {'sortable': False, 'filterable': True, 'facet': True},
        'collection_owner_url': {'sortable': False, 'filterable': False, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': True},
        'name_types': {'sortable': False, 'filterable': True, 'facet': True},
        'description_types': {'sortable': False, 'filterable': True, 'facet': True},
    }

    def dedupe_latest_versions(self):
        if self.is_versioned_object and self.is_latest_version:
            self.is_latest_version = False
            self.save(update_fields=['is_latest_version'])
        latest_versions = self.versions.filter(is_latest_version=True)
        count = latest_versions.count()
        if count > 1:
            for version in latest_versions.order_by('-id')[1:]:
                version.is_latest_version = False
                version.save(update_fields=['is_latest_version'])
        elif count < 1:
            version = self.versions.order_by('-id').first()
            version.is_latest_version = True
            version.save(update_fields=['is_latest_version'])

    @classmethod
    def duplicate_latest_versions(cls, limit=25, offset=0):
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                select mnemonic, count(*) from concepts where is_latest_version=true
                group by parent_id, mnemonic having count(*) > 1 order by mnemonic limit {limit} offset {offset}
                """
            )
            return named_tuple_fetchall(cursor)

    @staticmethod
    def get_search_document():
        from core.concepts.documents import ConceptDocument
        return ConceptDocument

    @property
    def concept(self):  # for url kwargs
        return self.mnemonic

    @staticmethod
    def get_resource_url_kwarg():
        return 'concept'

    @staticmethod
    def get_version_url_kwarg():
        return 'concept_version'

    @property
    def display_name(self):
        return get(self.preferred_locale, 'name')

    @property
    def display_locale(self):
        return get(self.preferred_locale, 'locale')

    @property
    def preferred_locale(self):
        try:
            return self.__get_parent_default_locale_name() or self.__get_parent_supported_locale_name() or \
                   self.__get_system_default_locale() or self.__get_preferred_locale() or \
                   self.__get_last_created_locale()
        except:  # pylint: disable=bare-except
            pass

        return None

    def __get_system_default_locale(self):
        system_default_locale = settings.DEFAULT_LOCALE

        return get(
            self.__names_qs(dict(locale=system_default_locale, locale_preferred=True), 'created_at', 'desc'), '0'
        ) or get(
            self.__names_qs(dict(locale=system_default_locale), 'created_at', 'desc'), '0'
        )

    def __get_parent_default_locale_name(self):
        parent_default_locale = self.parent.default_locale
        return get(
            self.__names_qs(dict(locale=parent_default_locale, locale_preferred=True), 'created_at', 'desc'), '0'
        ) or get(
            self.__names_qs(dict(locale=parent_default_locale), 'created_at', 'desc'), '0'
        )

    def __get_parent_supported_locale_name(self):
        parent_supported_locales = self.parent.supported_locales
        return get(
            self.__names_qs(dict(locale__in=parent_supported_locales, locale_preferred=True), 'created_at', 'desc'), '0'
        ) or get(
            self.__names_qs(dict(locale__in=parent_supported_locales), 'created_at', 'desc'), '0'
        )

    def __get_last_created_locale(self):
        return get(self.__names_qs({}, 'created_at', 'desc'), '0')

    def __get_preferred_locale(self):
        return get(
            self.__names_qs(dict(locale_preferred=True), 'created_at', 'desc'), '0'
        )

    def __names_qs(self, filters, order_by=None, order='desc'):
        if getattr(self, '_prefetched_objects_cache', None) and \
           'names' in self._prefetched_objects_cache:  # pragma: no cover
            return self.__names_from_prefetched_object_cache(filters, order_by, order)

        return self.__names_from_db(filters, order_by, order)

    def __names_from_db(self, filters, order_by=None, order='desc'):
        names = self.names.filter(
            **filters
        )
        if order_by:
            if order:
                order_by = '-' + order_by if order.lower() == 'desc' else order_by

            names = names.order_by(order_by)

        return names

    def __names_from_prefetched_object_cache(self, filters, order_by=None, order='desc'):  # pragma: no cover
        def is_eligible(name):
            return all(get(name, key) == value for key, value in filters.items())

        names = list(filter(is_eligible, self.names.all()))
        if order_by:
            names = sorted(names, key=lambda name: get(name, order_by), reverse=(order.lower() == 'desc'))
        return names

    @property
    def default_name_locales(self):
        return self.get_default_locales(self.names)

    @property
    def default_description_locales(self):
        return self.get_default_locales(self.descriptions)

    @staticmethod
    def get_default_locales(locales):
        return locales.filter(locale=settings.DEFAULT_LOCALE)

    @property
    def names_for_default_locale(self):
        return list(self.default_name_locales.values_list('name', flat=True))

    @property
    def descriptions_for_default_locale(self):
        return list(self.default_description_locales.values_list('name', flat=True))

    @property
    def iso_639_1_locale(self):
        return get(self.__names_qs(dict(type=ISO_639_1)), '0.name')

    @property
    def custom_validation_schema(self):
        return get(self, 'parent.custom_validation_schema')

    @property
    def all_names(self):
        return list(self.names.values_list('name', flat=True))

    @property
    def saved_unsaved_descriptions(self):
        unsaved_descriptions = get(self, 'cloned_descriptions', [])
        if self.id:
            return compact([*list(self.descriptions.all()), *unsaved_descriptions])
        return unsaved_descriptions

    @property
    def saved_unsaved_names(self):
        unsaved_names = get(self, 'cloned_names', [])

        if self.id:
            return compact([*list(self.names.all()), *unsaved_names])

        return unsaved_names

    @classmethod
    def get_base_queryset(cls, params):  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        queryset = cls.objects.filter(is_active=True)
        user = params.get('user', None)
        org = params.get('org', None)
        collection = params.get('collection', None)
        source = params.get('source', None)
        container_version = params.get('version', None)
        concept = params.get('concept', None)
        concept_version = params.get('concept_version', None)
        latest_released_version = None
        is_latest_released = container_version == LATEST
        if is_latest_released:
            filters = dict(user__username=user, organization__mnemonic=org)
            if source:
                from core.sources.models import Source
                latest_released_version = Source.find_latest_released_version_by(
                    {**filters, 'mnemonic': source})
            elif collection:
                from core.collections.models import Collection
                latest_released_version = Collection.find_latest_released_version_by(
                    {**filters, 'mnemonic': collection})

            if not latest_released_version:
                return cls.objects.none()

        if collection:
            queryset = queryset.filter(
                cls.get_filter_by_container_criterion(
                    'expansion_set__collection_version', collection, org, user, container_version,
                    is_latest_released, latest_released_version,
                )
            )
        if source:
            queryset = queryset.filter(
                cls.get_filter_by_container_criterion(
                    'sources', source, org, user, container_version,
                    is_latest_released, latest_released_version, 'parent'
                )
            )

        if concept:
            queryset = queryset.filter(mnemonic__in=cls.get_mnemonic_variations_for_filter(concept))
        if concept_version:
            queryset = queryset.filter(version=concept_version)

        return cls.apply_attribute_based_filters(queryset, params)

    @staticmethod
    def get_mnemonic_variations_for_filter(mnemonic):
        return [
            mnemonic, encode_string(mnemonic, safe=' '), encode_string(mnemonic, safe='+'),
            encode_string(mnemonic, safe='+%'), encode_string(mnemonic, safe='% +'),
            decode_string(mnemonic), decode_string(mnemonic, False)
        ]

    def clone(self):
        concept_version = Concept(
            mnemonic=self.mnemonic,
            version=generate_temp_version(),
            public_access=self.public_access,
            external_id=self.external_id,
            concept_class=self.concept_class,
            datatype=self.datatype,
            retired=self.retired,
            released=self.released,
            extras=self.extras or {},
            parent=self.parent,
            is_latest_version=self.is_latest_version,
            parent_id=self.parent_id,
            versioned_object_id=self.versioned_object_id,
            _index=self._index
        )
        concept_version.cloned_names = self.__clone_name_locales()
        concept_version.cloned_descriptions = self.__clone_description_locales()
        concept_version._parent_concepts = self.parent_concepts.all()  # pylint: disable=protected-access

        return concept_version

    @classmethod
    def version_for_concept(cls, concept, version_label, parent_version=None):
        version = concept.clone()
        version.version = version_label
        version.created_by_id = concept.created_by_id
        version.updated_by_id = concept.updated_by_id
        if parent_version:
            version.parent = parent_version
        version.released = False

        return version

    @classmethod
    def create_initial_version(cls, concept, **kwargs):
        initial_version = cls.version_for_concept(concept, generate_temp_version())
        initial_version.comment = concept.comment
        initial_version.save(**kwargs)
        initial_version.version = initial_version.id
        initial_version.released = True
        initial_version.is_latest_version = True
        initial_version.save()
        return initial_version

    @classmethod
    def create_new_version_for(
            cls, instance, data, user, create_parent_version=True, add_prev_version_children=True,
    ):  # pylint: disable=too-many-arguments
        instance.id = None  # Clear id so it is persisted as a new object
        instance.version = data.get('version', None)
        instance.concept_class = data.get('concept_class', instance.concept_class)
        instance.datatype = data.get('datatype', instance.datatype)
        instance.extras = data.get('extras', instance.extras)
        instance.external_id = data.get('external_id', instance.external_id)
        instance.comment = data.get('update_comment') or data.get('comment')
        instance.retired = data.get('retired', instance.retired)

        new_names = ConceptName.build(data.get('names', []))
        new_descriptions = ConceptDescription.build(data.get('descriptions', []))
        has_parent_concept_uris_attr = 'parent_concept_urls' in data
        parent_concept_uris = data.pop('parent_concept_urls', None)

        instance.cloned_names = compact(new_names)
        instance.cloned_descriptions = compact(new_descriptions)

        if not parent_concept_uris and has_parent_concept_uris_attr:
            parent_concept_uris = []

        return cls.persist_clone(instance, user, create_parent_version, parent_concept_uris, add_prev_version_children)

    def set_parent_concepts_from_uris(self, create_parent_version=True):
        parent_concepts = get(self, '_parent_concepts', None)
        if create_parent_version:
            for parent in parent_concepts:
                current_latest_version = parent.get_latest_version()
                parent_clone = parent.clone()
                Concept.create_new_version_for(
                    parent_clone,
                    dict(
                        names=[name.to_dict() for name in parent.names.all()],
                        descriptions=[desc.to_dict() for desc in parent.descriptions.all()],
                        parent_concept_urls=parent.parent_concept_urls,
                    ),
                    self.created_by,
                    create_parent_version=False
                )
                new_latest_version = parent.get_latest_version()
                for uri in current_latest_version.child_concept_urls:
                    Concept.objects.filter(
                        uri=uri
                    ).first().get_latest_version().parent_concepts.add(new_latest_version)

        if parent_concepts:
            self.parent_concepts.set([parent.get_latest_version() for parent in parent_concepts])
            self._parent_concepts = None

    def create_new_versions_for_removed_parents(self, uris):
        if uris:
            concepts = Concept.objects.filter(uri__in=uris)
            child_versioned_object_uri = drop_version(self.uri)
            for concept in concepts:
                current_latest_version = concept.get_latest_version()
                Concept.create_new_version_for(
                    concept.clone(),
                    dict(
                        names=[name.to_dict() for name in concept.names.all()],
                        descriptions=[desc.to_dict() for desc in concept.descriptions.all()],
                        parent_concept_urls=concept.parent_concept_urls
                    ),
                    concept.created_by,
                    create_parent_version=False,
                    add_prev_version_children=False
                )
                new_latest_version = concept.get_latest_version()
                for uri in current_latest_version.child_concept_urls:
                    if uri != child_versioned_object_uri:
                        child = Concept.objects.filter(uri=uri).first().get_latest_version()
                        child.parent_concepts.add(new_latest_version)

    def set_locales(self, locales, locale_klass):
        if not self.id:
            return  # pragma: no cover
        for locale in locales:
            new_locale = locale.clone() if isinstance(locale, locale_klass) else locale_klass.build(locale)
            new_locale.concept_id = self.id
            new_locale.save()

    def remove_locales(self):
        self.names.all().delete()
        self.descriptions.all().delete()

    def __clone_name_locales(self):
        return self.__clone_locales(self.names)

    def __clone_description_locales(self):
        return self.__clone_locales(self.descriptions)

    @staticmethod
    def __clone_locales(locales):
        return [locale.clone() for locale in locales.all()]

    def is_existing_in_parent(self):
        return self.parent.concepts_set.filter(mnemonic__exact=self.mnemonic).exists()

    @classmethod
    def persist_new(cls, data, user=None, create_initial_version=True, create_parent_version=True):  # pylint: disable=too-many-statements
        names = data.pop('names', []) or []
        descriptions = data.pop('descriptions', []) or []
        parent_concept_uris = data.pop('parent_concept_urls', None)
        concept = Concept(**data)
        temp_version = generate_temp_version()
        concept.version = temp_version
        if user:
            concept.created_by = concept.updated_by = user
        concept.errors = {}
        concept.mnemonic = concept.mnemonic or concept.version
        if concept.is_existing_in_parent():
            concept.errors = dict(__all__=[ALREADY_EXISTS])
            return concept

        try:
            concept.validate_locales_limit(names, descriptions)
            concept.save()
            concept.versioned_object_id = concept.id
            concept.version = str(concept.id)
            concept.set_locales(names, ConceptName)
            concept.set_locales(descriptions, ConceptDescription)

            parent_resource = concept.parent
            if startswith_temp_version(concept.mnemonic):
                concept.mnemonic = parent_resource.concept_mnemonic_next or str(concept.id)
                concept.name = concept.mnemonic
            if not concept.external_id:
                concept.external_id = parent_resource.concept_external_id_next
            concept.is_latest_version = not create_initial_version
            concept.public_access = parent_resource.public_access
            concept.save()
            concept.full_clean()

            initial_version = None
            if create_initial_version:
                initial_version = cls.create_initial_version(concept)
                if initial_version.id:
                    initial_version.set_locales(names, ConceptName)
                    initial_version.set_locales(descriptions, ConceptDescription)
                    initial_version.sources.set([parent_resource])

            concept.sources.set([parent_resource])
            concept.update_mappings()
            if parent_concept_uris:
                if get(settings, 'TEST_MODE', False):
                    process_hierarchy_for_new_concept(
                        concept.id, get(initial_version, 'id'), parent_concept_uris, create_parent_version)
                else:
                    process_hierarchy_for_new_concept.apply_async(
                        (concept.id, get(initial_version, 'id'), parent_concept_uris, create_parent_version),
                        queue='concurrent'
                    )
            if create_initial_version and concept._counted is True:
                parent_resource.update_concepts_count()
        except ValidationError as ex:
            if concept.id:
                concept.delete()
            concept.errors.update(ex.message_dict)
        except IntegrityError as ex:
            if concept.id:
                concept.delete()
            concept.errors.update(dict(__all__=ex.args))

        return concept

    def update_versioned_object(self):
        concept = self.versioned_object
        concept.extras = self.extras
        concept.remove_locales()
        concept.set_locales(self.names.all(), ConceptName)
        concept.set_locales(self.descriptions.all(), ConceptDescription)
        concept.parent_concepts.set(self.parent_concepts.all())
        concept.concept_class = self.concept_class
        concept.datatype = self.datatype
        concept.retired = self.retired
        concept.external_id = self.external_id or concept.external_id
        concept.save()

    @classmethod
    def persist_clone(
            cls, obj, user=None, create_parent_version=True, parent_concept_uris=None, add_prev_version_children=True,
            **kwargs
    ):  # pylint: disable=too-many-statements,too-many-branches,too-many-arguments
        errors = {}
        if not user:
            errors['version_created_by'] = PERSIST_CLONE_SPECIFY_USER_ERROR
            return errors
        obj.created_by = user
        obj.updated_by = user
        obj.version = obj.version or generate_temp_version()
        parent = obj.parent
        persisted = False
        versioned_object = obj.versioned_object
        prev_latest_version = versioned_object.versions.exclude(id=obj.id).filter(is_latest_version=True).first()
        try:
            with transaction.atomic():
                cls.validate_locales_limit(obj.cloned_names, obj.cloned_descriptions)

                cls.pause_indexing()

                obj.is_latest_version = True
                obj.save(**kwargs)
                if obj.id:
                    obj.version = str(obj.id)
                    obj.save()
                    obj.set_locales(obj.cloned_names, ConceptName)
                    obj.set_locales(obj.cloned_descriptions, ConceptDescription)
                    obj.cloned_names = []
                    obj.cloned_descriptions = []
                    obj.clean()  # clean here to validate locales that can only be saved after obj is saved
                    obj.update_versioned_object()
                    if prev_latest_version:
                        prev_latest_version._index = obj._index  # pylint: disable=protected-access
                        prev_latest_version.is_latest_version = False
                        prev_latest_version.save(update_fields=['is_latest_version', '_index'])
                        prev_latest_version.sources.remove(parent)
                        if add_prev_version_children:
                            if get(settings, 'TEST_MODE', False):
                                process_hierarchy_for_new_parent_concept_version(prev_latest_version.id, obj.id)
                            else:
                                process_hierarchy_for_new_parent_concept_version.apply_async(
                                    (prev_latest_version.id, obj.id),
                                    queue='concurrent'
                                )

                    obj.sources.set([parent])
                    persisted = True
                    cls.resume_indexing()
                    if get(settings, 'TEST_MODE', False):
                        process_hierarchy_for_concept_version(
                            obj.id, get(prev_latest_version, 'id'), parent_concept_uris, create_parent_version)
                    else:
                        process_hierarchy_for_concept_version.apply_async(
                            (obj.id, get(prev_latest_version, 'id'), parent_concept_uris, create_parent_version),
                            queue='concurrent'
                        )

                    def index_all():
                        if obj._index:  # pylint: disable=protected-access
                            if prev_latest_version:
                                prev_latest_version.index()
                            obj.index()

                    transaction.on_commit(index_all)
        except ValidationError as err:
            if get(err, 'error_dict'):
                errors.update(err.message_dict)
        finally:
            cls.resume_indexing()
            if not persisted:
                if prev_latest_version:
                    prev_latest_version._index = True  # pylint: disable=protected-access
                    prev_latest_version.is_latest_version = True
                    prev_latest_version.save(update_fields=['is_latest_version', '_index'])
                    prev_latest_version.sources.add(parent)
                if obj.id:
                    obj.remove_locales()
                    obj.delete()
                errors['non_field_errors'] = [PERSIST_CLONE_ERROR]

        return errors

    @staticmethod
    def validate_locales_limit(names, descriptions):
        if len(names) > MAX_LOCALES_LIMIT:
            raise ValidationError({'names': [MAX_NAMES_LIMIT]})
        if len(descriptions) > MAX_LOCALES_LIMIT:
            raise ValidationError({'descriptions': [MAX_DESCRIPTIONS_LIMIT]})

    def get_unidirectional_mappings_for_collection(self, collection_url, collection_version=HEAD):
        from core.mappings.models import Mapping
        return Mapping.objects.filter(
            from_concept__uri__icontains=drop_version(self.uri),
            expansion_set__collection_version__uri__icontains=collection_url,
            expansion_set__collection_version__version=collection_version
        )

    def get_indirect_mappings_for_collection(self, collection_url, collection_version=HEAD):
        from core.mappings.models import Mapping
        return Mapping.objects.filter(
            to_concept__uri__icontains=drop_version(self.uri),
            expansion_set__collection_version__uri__icontains=collection_url,
            expansion_set__collection_version__version=collection_version
        )

    def get_unidirectional_mappings(self):
        return self.__get_mappings_from_relation('mappings_from')

    def get_indirect_mappings(self):
        return self.__get_mappings_from_relation('mappings_to')

    def __get_mappings_from_relation(self, relation_manager, is_latest=False):
        from core.mappings.models import Mapping
        mappings = Mapping.objects.filter(parent_id=self.parent_id)

        if relation_manager == 'mappings_from':
            key = 'from_concept_id__in'
        else:
            key = 'to_concept_id__in'

        filters = {key: [self.id]}
        if self.is_latest_version:
            filters[key].append(self.versioned_object_id)
        elif self.is_versioned_object:
            latest_version = self.get_latest_version()
            filters[key].append(get(latest_version, 'id'))

        filters[key] = compact(filters[key])

        mappings = mappings.filter(**filters)

        if is_latest:
            return mappings.filter(is_latest_version=True)
        return mappings.filter(id=F('versioned_object_id'))

    def get_bidirectional_mappings(self):
        return self.get_unidirectional_mappings() | self.get_indirect_mappings()

    def get_bidirectional_mappings_for_collection(self, collection_url, collection_version=HEAD):
        queryset = self.get_unidirectional_mappings_for_collection(
            collection_url, collection_version
        ) | self.get_indirect_mappings_for_collection(
            collection_url, collection_version
        )

        return queryset.distinct()

    @staticmethod
    def get_latest_versions_for_queryset(concepts_qs):
        """Takes any concepts queryset and returns queryset of latest_version of each of those concepts"""

        if concepts_qs is None or not concepts_qs.exists():
            return Concept.objects.none()

        criteria_fields = list(concepts_qs.values('parent_id', 'mnemonic'))
        criterion = [models.Q(**attrs, is_latest_version=True) for attrs in criteria_fields]
        query = criterion.pop()
        for criteria in criterion:
            query |= criteria

        return Concept.objects.filter(query)

    def update_mappings(self):
        from core.mappings.models import Mapping
        parent_uris = compact([self.parent.uri, self.parent.canonical_url])
        for mapping in Mapping.objects.filter(
                to_concept_code=self.mnemonic, to_source_url__in=parent_uris, to_concept__isnull=True
        ):
            mapping.to_concept = self
            mapping.save()

        for mapping in Mapping.objects.filter(
                from_concept_code=self.mnemonic, from_source_url__in=parent_uris, from_concept__isnull=True
        ):
            mapping.from_concept = self
            mapping.save()

    @property
    def parent_concept_urls(self):
        return self.get_hierarchy_concept_urls('parent_concepts')

    @property
    def child_concept_urls(self):
        return self.get_hierarchy_concept_urls('child_concepts')

    def get_hierarchy_concept_urls(self, relation, versioned=False):
        queryset = get(self, relation).all()
        if self.is_latest_version:
            queryset |= get(self.versioned_object, relation).all()
        if self.is_versioned_object:
            latest_version = self.get_latest_version()
            if latest_version:
                queryset |= get(latest_version, relation).all()
        uris = queryset.values_list('uri', flat=True)
        if versioned:
            return self.__format_hierarchy_versioned_uris(uris)
        return self.__format_hierarchy_uris(uris)

    def get_hierarchy_queryset(self, relation, repo_version, filters=None):
        filters = filters or {}
        queryset = Concept.objects.filter(uri__in=self.get_hierarchy_concept_urls(relation, not repo_version.is_head))
        return queryset.filter(**filters)

    def child_concept_queryset(self):
        urls = self.child_concept_urls
        if urls:
            return Concept.objects.filter(uri__in=urls)
        return Concept.objects.none()

    @property
    def children_concepts_count(self):
        return len(self.child_concept_urls)

    @property
    def has_children(self):
        result = self.child_concepts.exists()

        if not result and self.is_latest_version:
            result = self.versioned_object.child_concepts.exists()
        elif not result and self.is_versioned_object:
            result = self.get_latest_version().child_concepts.exists()

        return result

    @property
    def parent_concepts_count(self):
        return len(self.parent_concept_urls)

    def parent_concept_queryset(self):
        urls = self.parent_concept_urls
        if urls:
            return Concept.objects.filter(uri__in=urls)
        return Concept.objects.none()

    @staticmethod
    def __format_hierarchy_uris(uris):
        return list({drop_version(uri) for uri in uris})

    @staticmethod
    def __format_hierarchy_versioned_uris(uris):
        return list({uri for uri in uris if is_versioned_uri(uri)})

    def get_hierarchy_path(self):
        result = []
        parent_concept = self.parent_concepts.first()
        while parent_concept is not None:
            result.append(drop_version(parent_concept.uri))
            parent_concept = parent_concept.parent_concepts.first()

        result.reverse()
        return result

    @staticmethod
    def __get_omit_from_version(omit_if_exists_in):
        if omit_if_exists_in:
            omit_from_version = ConceptContainerModel.resolve_expression_to_version(omit_if_exists_in)
            if omit_from_version.id:
                return omit_from_version

        return None

    def __get_omit_from_version_criteria(self, omit_if_exists_in):
        repo_version = self.__get_omit_from_version(omit_if_exists_in)
        criteria = models.Q()
        if repo_version:
            if repo_version.is_collection:
                expansion = get(repo_version, 'expansion')
                if expansion:
                    criteria = models.Q(expansion_set=expansion)
            else:
                criteria = models.Q(sources=repo_version)

        return criteria

    def cascade(  # pylint: disable=too-many-arguments,too-many-locals
            self, repo_version=None, source_mappings=True, source_to_concepts=True,
            mappings_criteria=None, cascade_mappings=True, cascade_hierarchy=True, cascade_levels=ALL,
            include_retired=False, reverse=False, return_map_types_criteria=None, omit_if_exists_in=None,
            max_results=1000
    ):
        from core.mappings.models import Mapping
        result = dict(concepts=Concept.objects.filter(id=self.id), mappings=Mapping.objects.none())

        if cascade_levels == 0:
            return result

        if not repo_version:
            return result
        if isinstance(repo_version, str):  # assumes its cascaded under source version, usage via collection-reference
            source_versions = self.sources.filter(version=repo_version)
            if source_versions.count() != 1:
                return result
            repo_version = source_versions.first()
            is_collection = False
        else:
            from core.collections.models import Collection
            is_collection = repo_version.__class__ == Collection

        omit_from_version_criteria = self.__get_omit_from_version_criteria(omit_if_exists_in)

        cascaded = []

        def iterate(level):
            if level == ALL or level > 0:
                if not cascaded or (result['concepts'].count() + result['mappings'].count()) < max_results:
                    not_cascaded = result['concepts'].exclude(
                        versioned_object_id__in=cascaded) if cascaded else result['concepts']
                    if not_cascaded.exists():
                        for concept in not_cascaded:
                            res = concept.get_cascaded_resources(
                                repo_version=repo_version,
                                source_mappings=source_mappings, source_to_concepts=source_to_concepts,
                                mappings_criteria=mappings_criteria, cascade_mappings=cascade_mappings,
                                cascade_hierarchy=cascade_hierarchy,
                                include_retired=include_retired, reverse=reverse, is_collection=is_collection,
                                return_map_types_criteria=return_map_types_criteria
                            )
                            cascaded.append(concept.versioned_object_id)
                            result['concepts'] = Concept.objects.filter(
                                id__in=[
                                    *res['concepts'].values_list('id', flat=True),
                                    *res['hierarchy_concepts'].values_list('id', flat=True),
                                    *result['concepts'].values_list('id', flat=True)
                                ]
                            ).exclude(omit_from_version_criteria)
                            result['mappings'] = Mapping.objects.filter(
                                id__in=[
                                    *res['mappings'].values_list('id', flat=True),
                                    *result['mappings'].values_list('id', flat=True)
                                ]
                            ).exclude(omit_from_version_criteria).order_by('map_type', 'sort_weight')

                        iterate(level if level == ALL else level - 1)

        iterate(cascade_levels)
        return result

    def cascade_as_hierarchy(  # pylint: disable=too-many-arguments,too-many-locals
            self, repo_version=None, source_mappings=True, source_to_concepts=True, mappings_criteria=None,
            cascade_mappings=True, cascade_hierarchy=True, cascade_levels=ALL,
            include_retired=False, reverse=False, return_map_types_criteria=None, omit_if_exists_in=None,
            _=None
    ):
        if cascade_levels == 0:
            return self

        if not repo_version:
            return self

        if isinstance(repo_version, str):  # assumes its cascaded under source version, may never happen
            source_versions = self.sources.filter(version=repo_version)
            if source_versions.count() != 1:
                return self
            repo_version = source_versions.first()
            is_collection = False
        else:
            from core.collections.models import Collection
            is_collection = repo_version.__class__ == Collection

        omit_from_version_criteria = self.__get_omit_from_version_criteria(omit_if_exists_in)

        self.current_level = 0
        levels = {self.current_level: [self]}

        def has_entries(entries):
            return entries is not None and (bool(len(entries['concepts'])) or entries['mappings'].exists())

        cascaded = {}

        def iterate(level):
            if level == ALL or level > 0:
                new_level = self.current_level + 1
                levels[new_level] = levels.get(new_level, [])
                for concept in levels[self.current_level]:
                    if concept.id in cascaded:
                        concept.terminal = not cascaded[concept.id]
                        continue
                    cascaded_entries = concept.get_cascaded_resources(
                        repo_version=repo_version,
                        source_mappings=source_mappings, source_to_concepts=source_to_concepts,
                        mappings_criteria=mappings_criteria, cascade_mappings=cascade_mappings,
                        cascade_hierarchy=cascade_hierarchy,
                        include_retired=include_retired, include_self=False,
                        reverse=reverse, is_collection=is_collection,
                        return_map_types_criteria=return_map_types_criteria
                    )
                    cascaded_entries['concepts'] = list(
                        set(list(cascaded_entries['hierarchy_concepts']) + list(cascaded_entries['concepts'])))

                    concept.cascaded_entries = cascaded_entries
                    concept.cascaded_entries['concepts'] = Concept.objects.filter(
                        id__in=[_concept.id for _concept in concept.cascaded_entries['concepts']]
                    ).exclude(omit_from_version_criteria)
                    concept.cascaded_entries['mappings'] = concept.cascaded_entries['mappings'].exclude(
                        omit_from_version_criteria)
                    concept_has_entries = has_entries(cascaded_entries)
                    cascaded[concept.id] = concept_has_entries
                    concept.terminal = not concept_has_entries
                    if level == 1 and cascade_levels != ALL:  # last level, when cascadeLevels are finite
                        for _concept in cascaded_entries['concepts']:
                            if _concept.id in cascaded:
                                _concept.terminal = not cascaded[_concept.id]

                    levels[new_level] += cascaded_entries['concepts']
                if not levels[new_level]:
                    return
                self.current_level += 1
                iterate(level if level == ALL else level - 1)

        iterate(cascade_levels)

        return self

    def get_cascaded_resources(self, **kwargs):
        if kwargs.pop('is_collection', None):
            return self.get_cascaded_resources_for_collection_version(**kwargs)
        return self.get_cascaded_resources_for_source_version(**kwargs)

    def get_cascaded_resources_for_source_version(self, **kwargs):
        if kwargs.pop('reverse', None):
            return self.cascaded_resources_reverse_for_source_version(**kwargs)
        return self.cascaded_resources_forward_for_source_version(**kwargs)

    def get_cascaded_resources_for_collection_version(self, **kwargs):
        if kwargs.pop('reverse', None):
            return self.cascaded_resources_reverse_for_collection_version(**kwargs)
        return self.cascaded_resources_forward_for_collection_version(**kwargs)

    def cascaded_resources_forward_for_source_version(  # pylint: disable=too-many-arguments,too-many-locals
            self, repo_version, source_mappings=True, source_to_concepts=True, mappings_criteria=None,
            cascade_mappings=True, cascade_hierarchy=True, include_retired=False,
            include_self=True, return_map_types_criteria=None
    ):
        from core.mappings.models import Mapping
        mappings = Mapping.objects.none()
        concepts = Concept.objects.filter(id=self.id) if include_self else Concept.objects.none()
        result = dict(concepts=concepts, mappings=mappings, hierarchy_concepts=Concept.objects.none())
        mappings_criteria = mappings_criteria or Q()  # cascade mappings criteria
        return_map_types_criteria = return_map_types_criteria if return_map_types_criteria is False else (
                return_map_types_criteria or Q()
        )
        if cascade_mappings and (source_mappings or source_to_concepts):
            mappings = repo_version.mappings.filter(
                from_concept_id__in={self.id, self.versioned_object_id}
            ).order_by('map_type')
            if repo_version.is_head:
                mappings = mappings.filter(id=F('versioned_object_id'))
            if not include_retired:
                mappings = mappings.filter(retired=False)
            if return_map_types_criteria is not False:
                result['mappings'] = mappings.filter(return_map_types_criteria).order_by('map_type', 'sort_weight')
        if source_to_concepts:
            if cascade_hierarchy:
                hierarchy_queryset = self.get_hierarchy_queryset(
                    'child_concepts', repo_version, dict(sources=repo_version))
                if not include_retired:
                    hierarchy_queryset = hierarchy_queryset.filter(retired=False)
                result['hierarchy_concepts'] = result['hierarchy_concepts'].union(hierarchy_queryset)
            to_cascade_mappings = mappings.filter(mappings_criteria)
            if to_cascade_mappings.exists():
                queryset = Concept.objects.filter(
                    id__in=to_cascade_mappings.values_list('to_concept_id', flat=True),
                    parent_id=self.parent_id
                )
                queryset = repo_version.concepts.filter(
                    versioned_object_id__in=queryset.values_list('versioned_object_id', flat=True))
                if repo_version.is_head:
                    queryset = queryset.filter(id=F('versioned_object_id'))
                if not include_retired:
                    queryset = queryset.filter(retired=False)
                result['concepts'] = result['concepts'].union(queryset)
        return result

    def cascaded_resources_reverse_for_source_version(  # pylint: disable=too-many-arguments,too-many-locals
            self, repo_version, source_mappings=True, source_to_concepts=True, mappings_criteria=None,
            cascade_mappings=True, cascade_hierarchy=True, include_retired=False,
            include_self=True, return_map_types_criteria=None
    ):
        from core.mappings.models import Mapping
        mappings = Mapping.objects.none()
        concepts = Concept.objects.filter(id=self.id) if include_self else Concept.objects.none()
        result = dict(concepts=concepts, mappings=mappings, hierarchy_concepts=Concept.objects.none())
        mappings_criteria = mappings_criteria or Q()  # cascade mappings criteria
        return_map_types_criteria = return_map_types_criteria if return_map_types_criteria is False else (
                return_map_types_criteria or Q()
        )
        if cascade_mappings and (source_mappings or source_to_concepts):
            mappings = repo_version.mappings.filter(
                to_concept_id__in={self.id, self.versioned_object_id}
            ).order_by('map_type')
            if repo_version.is_head:
                mappings = mappings.filter(id=F('versioned_object_id'))
            if not include_retired:
                mappings = mappings.filter(retired=False)
            if return_map_types_criteria is not False:
                result['mappings'] = mappings.filter(return_map_types_criteria).order_by('map_type', 'sort_weight')
        if source_to_concepts:
            if cascade_hierarchy:
                hierarchy_queryset = self.get_hierarchy_queryset(
                    'parent_concepts', repo_version, dict(sources=repo_version))
                if not include_retired:
                    hierarchy_queryset = hierarchy_queryset.filter(retired=False)
                result['hierarchy_concepts'] = result['hierarchy_concepts'].union(hierarchy_queryset)
            to_cascade_mappings = mappings.filter(mappings_criteria)
            if to_cascade_mappings.exists():
                queryset = Concept.objects.filter(
                    id__in=to_cascade_mappings.values_list('from_concept_id', flat=True), parent_id=self.parent_id)
                queryset = repo_version.concepts.filter(versioned_object_id__in=queryset)
                if repo_version.is_head:
                    queryset = queryset.filter(id=F('versioned_object_id'))
                if not include_retired:
                    queryset = queryset.filter(retired=False)
                result['concepts'] = result['concepts'].union(queryset)

        return result

    def cascaded_resources_forward_for_collection_version(  # pylint: disable=too-many-arguments,too-many-locals
            self, repo_version, source_mappings=True, source_to_concepts=True, mappings_criteria=None,
            cascade_mappings=True, cascade_hierarchy=True, include_retired=False,
            include_self=True, return_map_types_criteria=None
    ):
        from core.mappings.models import Mapping
        mappings = Mapping.objects.none()
        concepts = Concept.objects.filter(id=self.id) if include_self else Concept.objects.none()
        result = dict(concepts=concepts, mappings=mappings, hierarchy_concepts=Concept.objects.none())
        mappings_criteria = mappings_criteria or Q()  # cascade mappings criteria
        return_map_types_criteria = return_map_types_criteria if return_map_types_criteria is False else (
                return_map_types_criteria or Q()
        )
        expansion = repo_version.expansion
        if not expansion:
            return result
        if cascade_mappings and (source_mappings or source_to_concepts):
            mappings = expansion.mappings.filter(
                from_concept_id__in={self.id, self.versioned_object_id}
            ).order_by('map_type')
            if not include_retired:
                mappings = mappings.filter(retired=False)
            if return_map_types_criteria is not False:
                result['mappings'] = mappings.filter(return_map_types_criteria).order_by('map_type', 'sort_weight')
        if source_to_concepts:
            if cascade_hierarchy:
                hierarchy_queryset = self.get_hierarchy_queryset(
                    'child_concepts', repo_version, dict(expansion_set__collection_version=repo_version))
                if not include_retired:
                    hierarchy_queryset = hierarchy_queryset.filter(retired=False)
                result['hierarchy_concepts'] = result['hierarchy_concepts'].union(hierarchy_queryset)
            to_cascade_mappings = mappings.filter(mappings_criteria)
            if to_cascade_mappings.exists():
                queryset = Concept.objects.filter(id__in=to_cascade_mappings.values_list('to_concept_id', flat=True))
                queryset = expansion.concepts.filter(
                    versioned_object_id__in=queryset.values_list('versioned_object_id', flat=True))
                if not include_retired:
                    queryset = queryset.filter(retired=False)
                result['concepts'] = result['concepts'].union(queryset)
        return result

    def cascaded_resources_reverse_for_collection_version(  # pylint: disable=too-many-arguments,too-many-locals
            self, repo_version, source_mappings=True, source_to_concepts=True, mappings_criteria=None,
            cascade_mappings=True, cascade_hierarchy=True, include_retired=False,
            include_self=True, return_map_types_criteria=None
    ):
        from core.mappings.models import Mapping
        mappings = Mapping.objects.none()
        concepts = Concept.objects.filter(id=self.id) if include_self else Concept.objects.none()
        result = dict(concepts=concepts, mappings=mappings, hierarchy_concepts=Concept.objects.none())
        mappings_criteria = mappings_criteria or Q()  # cascade mappings criteria
        return_map_types_criteria = return_map_types_criteria if return_map_types_criteria is False else (
                return_map_types_criteria or Q()
        )
        expansion = repo_version.expansion

        if not expansion:
            return result

        if cascade_mappings and (source_mappings or source_to_concepts):
            mappings = expansion.mappings.filter(
                to_concept_id__in={self.id, self.versioned_object_id}
            ).order_by('map_type')
            if not include_retired:
                mappings = mappings.filter(retired=False)
            if return_map_types_criteria is not False:
                result['mappings'] = mappings.filter(return_map_types_criteria).order_by('map_type', 'sort_weight')
        if source_to_concepts:
            if cascade_hierarchy:
                hierarchy_queryset = self.get_hierarchy_queryset(
                    'parent_concepts', repo_version, dict(expansion_set__collection_version=repo_version))
                if not include_retired:
                    hierarchy_queryset = hierarchy_queryset.filter(retired=False)
                result['hierarchy_concepts'] = result['hierarchy_concepts'].union(hierarchy_queryset)
            to_cascade_mappings = mappings.filter(mappings_criteria)
            if to_cascade_mappings.exists():
                queryset = Concept.objects.filter(id__in=to_cascade_mappings.values_list('from_concept_id', flat=True))
                queryset = expansion.concepts.filter(versioned_object_id__in=queryset)
                if not include_retired:
                    queryset = queryset.filter(retired=False)
                result['concepts'] = result['concepts'].union(queryset)

        return result

    @staticmethod
    def get_serializer_class(verbose=False, version=False, brief=False, cascade=False):
        if brief:
            from core.concepts.serializers import ConceptMinimalSerializer, ConceptCascadeMinimalSerializer
            return ConceptCascadeMinimalSerializer if cascade else ConceptMinimalSerializer
        if version:
            from core.concepts.serializers import ConceptVersionDetailSerializer, ConceptVersionListSerializer
            if cascade:
                from core.concepts.serializers import ConceptVersionCascadeSerializer
                return ConceptVersionCascadeSerializer
            return ConceptVersionDetailSerializer if verbose else ConceptVersionListSerializer

        from core.concepts.serializers import ConceptDetailSerializer, ConceptListSerializer
        return ConceptDetailSerializer if verbose else ConceptListSerializer
