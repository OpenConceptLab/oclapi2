from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, IntegrityError, transaction, connection
from django.db.models import F, Q
from pydash import get, compact

from core.common.constants import ISO_639_1, INCLUDE_RETIRED_PARAM, LATEST, HEAD
from core.common.mixins import SourceChildMixin
from core.common.models import VersionedModel
from core.common.tasks import process_hierarchy_for_new_concept, process_hierarchy_for_concept_version, \
    process_hierarchy_for_new_parent_concept_version
from core.common.utils import parse_updated_since_param, generate_temp_version, drop_version, \
    encode_string, decode_string, named_tuple_fetchall
from core.concepts.constants import CONCEPT_TYPE, LOCALES_FULLY_SPECIFIED, LOCALES_SHORT, LOCALES_SEARCH_INDEX_TERM, \
    CONCEPT_WAS_RETIRED, CONCEPT_IS_ALREADY_RETIRED, CONCEPT_IS_ALREADY_NOT_RETIRED, CONCEPT_WAS_UNRETIRED, \
    PERSIST_CLONE_ERROR, PERSIST_CLONE_SPECIFY_USER_ERROR, ALREADY_EXISTS, CONCEPT_REGEX, MAX_LOCALES_LIMIT, \
    MAX_NAMES_LIMIT, MAX_DESCRIPTIONS_LIMIT
from core.concepts.mixins import ConceptValidationMixin


class LocalizedText(models.Model):
    class Meta:
        db_table = 'localized_texts'

        indexes = [
            models.Index(fields=['name']),
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

    @classmethod
    def get_dormant_queryset(cls):
        return cls.objects.filter(name_locales__isnull=True, description_locales__isnull=True)

    @classmethod
    def dormants(cls, raw=True):
        if raw:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT("localized_texts"."id") FROM "localized_texts"
                    WHERE NOT EXISTS (SELECT 1 FROM "concepts_names" WHERE
                    "concepts_names"."localizedtext_id" = "localized_texts"."id")
                    AND NOT EXISTS (SELECT 1 FROM "concepts_descriptions"
                    WHERE "concepts_descriptions"."localizedtext_id" = "localized_texts"."id")
                    """
                )
                count, = cursor.fetchone()
                return count

        return cls.get_dormant_queryset().count()

    def to_dict(self):
        return dict(
            external_id=self.external_id, name=self.name, type=self.type, locale=self.locale,
            locale_preferred=self.locale_preferred,
        )

    def clone(self):
        return LocalizedText(
            external_id=self.external_id,
            name=self.name,
            type=self.type,
            locale=self.locale,
            locale_preferred=self.locale_preferred
        )

    @classmethod
    def build(cls, params, used_as='name'):
        instance = None
        if used_as == 'name':
            instance = cls.build_name(params)
        if used_as == 'description':
            instance = cls.build_description(params)

        return instance

    @classmethod
    def build_name(cls, params):
        _type = params.pop('type', None)
        name_type = params.pop('name_type', None)
        if not name_type or name_type == 'ConceptName':
            name_type = _type

        return cls(
            **{**params, 'type': name_type}
        )

    @classmethod
    def build_description(cls, params):
        _type = params.pop('type', None)
        description_type = params.pop('description_type', None)
        if not description_type or description_type == 'ConceptDescription':
            description_type = _type

        description_name = params.pop('description', None) or params.pop('name', None)
        return cls(
            **{
                **params,
                'type': description_type,
                'name': description_name,
            }
        )

    @classmethod
    def build_locales(cls, locale_params, used_as='name'):
        if not locale_params:
            return []

        return [cls.build(locale, used_as) for locale in locale_params]

    @property
    def is_fully_specified(self):
        return self.type in LOCALES_FULLY_SPECIFIED

    @property
    def is_short(self):
        return self.type in LOCALES_SHORT

    @property
    def is_search_index_term(self):
        return self.type in LOCALES_SEARCH_INDEX_TERM


class HierarchicalConcepts(models.Model):
    child = models.ForeignKey('concepts.Concept', related_name='child_parent', on_delete=models.CASCADE)
    parent = models.ForeignKey('concepts.Concept', related_name='parent_child', on_delete=models.CASCADE)


class Concept(ConceptValidationMixin, SourceChildMixin, VersionedModel):  # pylint: disable=too-many-public-methods
    class Meta:
        db_table = 'concepts'
        unique_together = ('mnemonic', 'version', 'parent')
        indexes = [
            models.Index(name='concepts_updated_6490d8_idx', fields=['-updated_at'],
                         condition=(Q(is_active=True) & Q(retired=False) & Q(is_latest_version=True) &
                                    ~Q(public_access='None'))),
        ] + VersionedModel.Meta.indexes

    external_id = models.TextField(null=True, blank=True)
    concept_class = models.TextField()
    datatype = models.TextField()
    names = models.ManyToManyField(LocalizedText, related_name='name_locales')
    descriptions = models.ManyToManyField(LocalizedText, related_name='description_locales')
    comment = models.TextField(null=True, blank=True)
    parent = models.ForeignKey('sources.Source', related_name='concepts_set', on_delete=models.CASCADE)
    sources = models.ManyToManyField('sources.Source', related_name='concepts')
    versioned_object = models.ForeignKey(
        'self', related_name='versions_set', null=True, blank=True, on_delete=models.CASCADE
    )
    parent_concepts = models.ManyToManyField(
        'self', through='HierarchicalConcepts', symmetrical=False, related_name='child_concepts'
    )
    logo_path = None
    mnemonic = models.CharField(
        max_length=255, validators=[RegexValidator(regex=CONCEPT_REGEX)],
        db_index=True
    )

    OBJECT_TYPE = CONCEPT_TYPE
    ALREADY_RETIRED = CONCEPT_IS_ALREADY_RETIRED
    ALREADY_NOT_RETIRED = CONCEPT_IS_ALREADY_NOT_RETIRED
    WAS_RETIRED = CONCEPT_WAS_RETIRED
    WAS_UNRETIRED = CONCEPT_WAS_UNRETIRED

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
        'retired': {'sortable': False, 'filterable': True, 'facet': True},
        'source': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'collection': {'sortable': False, 'filterable': True, 'facet': True},
        'collection_owner_url': {'sortable': False, 'filterable': False, 'facet': True},
        'owner': {'sortable': True, 'filterable': True, 'facet': True, 'exact': True},
        'owner_type': {'sortable': False, 'filterable': True, 'facet': True, 'exact': True},
        'external_id': {'sortable': False, 'filterable': True, 'facet': False, 'exact': False},
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
        return self.mnemonic  # pragma: no cover

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
        is_latest = params.get('is_latest', None) in [True, 'true']
        include_retired = params.get(INCLUDE_RETIRED_PARAM, None) in [True, 'true']
        updated_since = parse_updated_since_param(params)
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
                    'collection_set', collection, org, user, container_version,
                    is_latest_released, latest_released_version,
                )
            )
        if source:
            queryset = queryset.filter(
                cls.get_filter_by_container_criterion(
                    'sources', source, org, user, container_version,
                    is_latest_released, latest_released_version
                )
            )

        if concept:
            mnemonics = [concept, encode_string(concept, safe=' '), encode_string(concept, safe='+'),
                         encode_string(concept, safe='+%'), encode_string(concept, safe='% +'),
                         decode_string(concept), decode_string(concept, False)]
            queryset = queryset.filter(mnemonic__in=mnemonics)
        if concept_version:
            queryset = queryset.filter(version=concept_version)
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)
        if not include_retired and not concept:
            queryset = queryset.filter(retired=False)
        if updated_since:
            queryset = queryset.filter(updated_at__gte=updated_since)

        return queryset

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
        initial_version.save(**kwargs)
        initial_version.version = initial_version.id
        initial_version.released = True
        initial_version.is_latest_version = True
        initial_version.save()
        return initial_version

    @classmethod
    def create_new_version_for(cls, instance, data, user, create_parent_version=True, add_prev_version_children=True):  # pylint: disable=too-many-arguments
        instance.concept_class = data.get('concept_class', instance.concept_class)
        instance.datatype = data.get('datatype', instance.datatype)
        instance.extras = data.get('extras', instance.extras)
        instance.external_id = data.get('external_id', instance.external_id)
        instance.comment = data.get('update_comment') or data.get('comment')
        instance.retired = data.get('retired', instance.retired)

        new_names = LocalizedText.build_locales(data.get('names', []))
        new_descriptions = LocalizedText.build_locales(data.get('descriptions', []), 'description')
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
                        uri__contains=uri, is_latest_version=True
                    ).first().parent_concepts.add(new_latest_version)

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
                        child = Concept.objects.filter(uri__contains=uri, is_latest_version=True).first()
                        child.parent_concepts.add(new_latest_version)

    def set_locales(self):
        if not self.id:
            return  # pragma: no cover

        names = get(self, 'cloned_names', [])
        descriptions = get(self, 'cloned_descriptions', [])

        for name in names:
            name.save()
        for desc in descriptions:
            desc.save()

        self.names.set(names)
        self.descriptions.set(descriptions)
        self.cloned_names = []
        self.cloned_descriptions = []

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
    def persist_new(cls, data, user=None, create_initial_version=True, create_parent_version=True):
        names = [
            name if isinstance(name, LocalizedText) else LocalizedText.build(
                name
            ) for name in data.pop('names', []) or []
        ]
        descriptions = [
            desc if isinstance(desc, LocalizedText) else LocalizedText.build(
                desc, 'description'
            ) for desc in data.pop('descriptions', []) or []
        ]

        parent_concept_uris = data.pop('parent_concept_urls', None)
        concept = Concept(**data)
        concept.version = generate_temp_version()
        if user:
            concept.created_by = concept.updated_by = user
        concept.errors = {}
        if concept.is_existing_in_parent():
            concept.errors = dict(__all__=[ALREADY_EXISTS])
            return concept

        try:
            concept.validate_locales_limit(names, descriptions)
            concept.cloned_names = names
            concept.cloned_descriptions = descriptions
            concept.full_clean()
            concept.save()
            concept.versioned_object_id = concept.id
            concept.version = str(concept.id)
            concept.is_latest_version = not create_initial_version
            parent_resource = concept.parent
            parent_resource_head = parent_resource.head
            concept.public_access = parent_resource.public_access
            concept.save()
            concept.set_locales()

            initial_version = None
            if create_initial_version:
                initial_version = cls.create_initial_version(concept)
                initial_version.names.set(concept.names.all())
                initial_version.descriptions.set(concept.descriptions.all())
                initial_version.sources.set([parent_resource, parent_resource_head])

            concept.sources.set([parent_resource, parent_resource_head])
            concept.update_mappings()
            if parent_concept_uris:
                if get(settings, 'TEST_MODE', False):
                    process_hierarchy_for_new_concept(
                        concept.id, get(initial_version, 'id'), parent_concept_uris, create_parent_version)
                else:
                    process_hierarchy_for_new_concept.delay(
                        concept.id, get(initial_version, 'id'), parent_concept_uris, create_parent_version)
        except ValidationError as ex:
            concept.errors.update(ex.message_dict)
        except IntegrityError as ex:
            concept.errors.update(dict(__all__=ex.args))

        return concept

    def update_versioned_object(self):
        concept = self.versioned_object
        concept.extras = self.extras
        concept.names.set(self.names.all())
        concept.descriptions.set(self.descriptions.all())
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
        parent_head = parent.head
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
                    obj.set_locales()
                    obj.clean()  # clean here to validate locales that can only be saved after obj is saved
                    obj.update_versioned_object()
                    if prev_latest_version:
                        prev_latest_version.is_latest_version = False
                        prev_latest_version.save(update_fields=['is_latest_version'])
                        if add_prev_version_children:
                            if get(settings, 'TEST_MODE', False):
                                process_hierarchy_for_new_parent_concept_version(prev_latest_version.id, obj.id)
                            else:
                                process_hierarchy_for_new_parent_concept_version.delay(prev_latest_version.id, obj.id)

                    obj.sources.set(compact([parent, parent_head]))
                    persisted = True
                    cls.resume_indexing()
                    if get(settings, 'TEST_MODE', False):
                        process_hierarchy_for_concept_version(
                            obj.id, get(prev_latest_version, 'id'), parent_concept_uris, create_parent_version)
                    else:
                        process_hierarchy_for_concept_version.delay(
                            obj.id, get(prev_latest_version, 'id'), parent_concept_uris, create_parent_version)

                    def index_all():
                        if prev_latest_version:
                            prev_latest_version.index()
                        obj.index()

                    transaction.on_commit(index_all)
        except ValidationError as err:
            errors.update(err.message_dict)
        finally:
            cls.resume_indexing()
            if not persisted:
                if prev_latest_version:
                    prev_latest_version.is_latest_version = True
                    prev_latest_version.save(update_fields=['is_latest_version'])
                if obj.id:
                    obj.remove_locales()
                    obj.sources.remove(parent_head)
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
            from_concept__uri__icontains=drop_version(self.uri), collection_set__uri__icontains=collection_url,
            collection_set__version=collection_version
        )

    def get_indirect_mappings_for_collection(self, collection_url, collection_version=HEAD):
        from core.mappings.models import Mapping
        return Mapping.objects.filter(
            to_concept__uri__icontains=drop_version(self.uri), collection_set__uri__icontains=collection_url,
            collection_set__version=collection_version
        )

    def get_unidirectional_mappings(self):
        return self.__get_mappings_from_relation('mappings_from')

    def get_latest_unidirectional_mappings(self):
        return self.__get_mappings_from_relation('mappings_from', True)

    def get_indirect_mappings(self):
        return self.__get_mappings_from_relation('mappings_to')

    def __get_mappings_from_relation(self, relation_manager, is_latest=False):
        queryset = getattr(self, relation_manager).filter(parent_id=self.parent_id)

        if self.is_versioned_object:
            latest_version = self.get_latest_version()
            if latest_version:
                queryset |= getattr(latest_version, relation_manager).filter(parent_id=self.parent_id)
        if self.is_latest_version:
            versioned_object = self.versioned_object
            if versioned_object:
                queryset |= getattr(versioned_object, relation_manager).filter(parent_id=self.parent_id)

        if is_latest:
            return queryset.filter(is_latest_version=True)

        return queryset.filter(id=F('versioned_object_id')).order_by('-updated_at').distinct('updated_at')

    def get_bidirectional_mappings(self):
        queryset = self.get_unidirectional_mappings() | self.get_indirect_mappings()

        return queryset.distinct()

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
        queryset = self.parent_concepts.all()
        if self.is_latest_version:
            queryset |= self.versioned_object.parent_concepts.all()
        if self.is_versioned_object:
            queryset |= self.get_latest_version().parent_concepts.all()
        return self.__format_hierarchy_uris(queryset.values_list('uri', flat=True))

    @property
    def child_concept_urls(self):
        queryset = self.child_concepts.all()
        if self.is_latest_version:
            queryset |= self.versioned_object.child_concepts.all()
        if self.is_versioned_object:
            queryset |= self.get_latest_version().child_concepts.all()
        return self.__format_hierarchy_uris(queryset.values_list('uri', flat=True))

    def child_concept_queryset(self):
        urls = self.child_concept_urls
        if urls:
            return Concept.objects.filter(uri__in=urls)
        return Concept.objects.none()

    def parent_concept_queryset(self):
        urls = self.parent_concept_urls
        if urls:
            return Concept.objects.filter(uri__in=urls)
        return Concept.objects.none()

    @staticmethod
    def __format_hierarchy_uris(uris):
        return list({drop_version(uri) for uri in uris})

    def get_hierarchy_path(self):
        result = []
        parent_concept = self.parent_concepts.first()
        while parent_concept is not None:
            result.append(drop_version(parent_concept.uri))
            parent_concept = parent_concept.parent_concepts.first()

        result.reverse()
        return result

    def delete(self, using=None, keep_parents=False):
        LocalizedText.objects.filter(name_locales=self).delete()
        LocalizedText.objects.filter(description_locales=self).delete()
        return super().delete(using=using, keep_parents=keep_parents)
