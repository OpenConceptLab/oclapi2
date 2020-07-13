
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, IntegrityError
from django.db.models import F
from pydash import get, compact

from core.common.constants import TEMP, HEAD, ISO_639_1
from core.common.models import VersionedModel
from core.common.utils import reverse_resource
from core.concepts.constants import CONCEPT_TYPE, LOCALES_FULLY_SPECIFIED, LOCALES_SHORT, LOCALES_SEARCH_INDEX_TERM, \
    CONCEPT_WAS_RETIRED, CONCEPT_IS_ALREADY_RETIRED, CONCEPT_IS_ALREADY_NOT_RETIRED, CONCEPT_WAS_UNRETIRED
from core.concepts.mixins import ConceptValidationMixin


class LocalizedText(models.Model):
    class Meta:
        db_table = 'localized_texts'

    external_id = models.TextField(null=True, blank=True)
    name = models.TextField()
    type = models.TextField(null=True, blank=True)
    locale = models.TextField()
    locale_preferred = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

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
        return LocalizedText(
            **{**params, 'type': params.pop('name_type', params.pop('type', None))}
        )

    @classmethod
    def build_description(cls, params):
        return LocalizedText(
            **{
                **params,
                'type': params.pop('description_type', params.pop('type', None)),
                'name': params.pop('description', params.pop('name', None)),
            }
        )

    @staticmethod
    def get_filter_criteria_for_attribute(attribute):
        if attribute == 'is_fully_specified':
            return dict(type__in=LOCALES_FULLY_SPECIFIED)
        if attribute == 'is_short':
            return dict(type__in=LOCALES_SHORT)
        if attribute == 'is_search_index_term':
            return dict(type__in=LOCALES_SEARCH_INDEX_TERM)
        return {attribute: True}

    @property
    def is_fully_specified(self):
        return self.type in LOCALES_FULLY_SPECIFIED

    @property
    def is_short(self):
        return self.type in LOCALES_SHORT

    @property
    def is_search_index_term(self):
        return self.type in LOCALES_SEARCH_INDEX_TERM


class Concept(ConceptValidationMixin, VersionedModel):  # pylint: disable=too-many-public-methods
    class Meta:
        db_table = 'concepts'
        unique_together = ('mnemonic', 'version', 'parent')

    external_id = models.TextField(null=True, blank=True)
    concept_class = models.TextField()
    datatype = models.TextField()
    names = models.ManyToManyField(LocalizedText, related_name='name_locales')
    descriptions = models.ManyToManyField(LocalizedText, related_name='description_locales')
    comment = models.TextField(null=True, blank=True)
    parent = models.ForeignKey('sources.Source', related_name='concepts_set', on_delete=models.DO_NOTHING)
    sources = models.ManyToManyField('sources.Source', related_name='concepts')

    OBJECT_TYPE = CONCEPT_TYPE

    @property
    def concept(self):
        return self.mnemonic

    @staticmethod
    def get_resource_url_kwarg():
        return 'concept'

    @staticmethod
    def get_version_url_kwarg():
        return 'concept_version'

    @property
    def owner(self):
        return self.parent.parent

    @property
    def owner_name(self):
        return str(self.owner)

    @property
    def owner_type(self):
        return self.owner.resource_type

    @property
    def owner_url(self):
        return self.owner.url

    @property
    def parent_resource(self):
        return self.parent.mnemonic

    @property
    def display_name(self):
        return get(self.preferred_locale, 'name')

    @property
    def display_locale(self):
        return get(self.preferred_locale, 'locale')

    @property
    def preferred_locale(self):
        locales = self.preferred_name_locales or self.names
        return locales.order_by('-created_at').first()

    @property
    def preferred_name_locales(self):
        return self.names.filter(locale_preferred=True)

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
        return get(self.names.filter(type=ISO_639_1).first(), 'name')

    @property
    def custom_validation_schema(self):
        return get(self, 'parent.custom_validation_schema')

    @property
    def versions_url(self):
        return reverse_resource(self, 'concept-version-list')

    @property
    def fully_specified_names(self):
        return self.names.filter(
            **LocalizedText.get_filter_criteria_for_attribute('is_fully_specified')
        )

    @property
    def short_names(self):
        return self.names.filter(
            **LocalizedText.get_filter_criteria_for_attribute('is_short')
        )

    @property
    def non_short_names(self):
        return self.names.exclude(
            **LocalizedText.get_filter_criteria_for_attribute('is_short')
        )

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
    def get_base_queryset(cls, params):
        queryset = cls.objects.filter(is_active=True)
        user = params.get('user', None)
        org = params.get('org', None)
        source = params.get('source', None)
        source_version = params.get('version', None)
        concept = params.get('concept', None)
        concept_version = params.get('concept_version', None)
        is_latest = params.get('is_latest', None)
        if user:
            queryset = queryset.filter(parent__user__username=user)
        if org:
            queryset = queryset.filter(parent__organization__mnemonic=org)
        if source:
            queryset = queryset.filter(sources__mnemonic=source)
        if source_version:
            queryset = queryset.filter(sources__version=source_version)
        if concept:
            queryset = queryset.filter(mnemonic=concept)
        if concept_version:
            queryset = queryset.filter(version=concept_version)
        if is_latest:
            queryset = queryset.filter(is_latest_version=True)

        return queryset.distinct()

    def clone(self):
        concept_version = Concept(
            mnemonic=self.mnemonic,
            version=TEMP,
            public_access=self.public_access,
            external_id=self.external_id,
            concept_class=self.concept_class,
            datatype=self.datatype,
            retired=self.retired,
            released=self.released,
            extras=self.extras or dict(),
            parent=self.parent,
            is_latest_version=self.is_latest_version,
            parent_id=self.parent_id,
        )
        concept_version.cloned_names = self.__clone_name_locales()
        concept_version.cloned_descriptions = self.__clone_description_locales()

        return concept_version

    @classmethod
    def version_for_concept(cls, concept, version_label, parent_version=None):
        version = concept.clone()
        version.version = version_label
        version.created_by_id = concept.created_by_id
        version.updated_by_id = concept.updated_by_id
        version.parent = parent_version
        version.released = False

        return version

    def set_locales(self):
        if not self.id:
            return

        names = get(self, 'cloned_names', [])
        descriptions = get(self, 'cloned_descriptions', [])

        for name in names:
            if not name.id:
                name.save()
        for desc in descriptions:
            if not desc.id:
                desc.save()

        self.names.set(names)
        self.descriptions.set(descriptions)

    def remove_locales(self):
        self.names.all().delete()
        self.descriptions.all().delete()

    def __clone_name_locales(self):
        return [name.clone() for name in self.names.all()]

    def __clone_description_locales(self):
        return [desc.clone() for desc in self.descriptions.all()]

    @classmethod
    def persist_new(cls, data, user=None):
        names = [
            name if isinstance(name, LocalizedText) else LocalizedText.build(name) for name in data.pop('names', [])
        ]
        descriptions = [
            desc if isinstance(desc, LocalizedText) else LocalizedText.build(
                desc, 'description'
            ) for desc in data.pop('descriptions', [])
        ]
        concept = Concept(**{**data, 'version': HEAD})
        if user:
            concept.created_by = concept.updated_by = user
        concept.errors = dict()

        try:
            concept.cloned_names = names
            concept.cloned_descriptions = descriptions
            concept.full_clean()
            concept.save()
        except ValidationError as ex:
            concept.errors.update(ex.message_dict)
        except IntegrityError as ex:
            concept.errors.update(dict(__all__=ex.args))

        if concept.id:
            concept.set_locales()
            parent_resource = concept.parent
            parent_resource_head = parent_resource.head
            concept.sources.set([parent_resource, parent_resource_head])

            # to update counts
            parent_resource.save()
            parent_resource_head.save()

        return concept

    @classmethod
    def persist_clone(cls, obj, user=None, **kwargs):
        errors = dict()
        if not user:
            errors['version_created_by'] = 'Must specify which user is attempting to create a new concept version.'
            return errors
        obj.created_by = user
        parent = obj.parent
        parent_head = parent.head
        persisted = False
        errored_action = 'saving new concept version'
        latest_versions = None
        head_versions = None
        try:
            obj.save(**kwargs)
            obj.set_locales()
            obj.clean()  # clean here to validate locales that can only be saved after obj is saved
            latest_versions = obj.versions.filter(is_latest_version=True)
            latest_versions.update(is_latest_version=False)
            head_versions = obj.versions.filter(version=HEAD)
            head_versions.update(version=F('id'))
            obj.is_latest_version = True
            obj.version = HEAD
            obj.save()
            obj.sources.set(compact([parent, parent_head]))

            # to update counts
            parent.save()
            parent_head.save()

            persisted = True
        except ValidationError as err:
            errors.update(err.message_dict)
        finally:
            if not persisted:
                obj.remove_locales()
                obj.sources.remove(parent_head)
                if latest_versions:
                    latest_versions.update(is_latest_version=True)
                if head_versions:
                    head_versions.update(version=HEAD)
                if obj.id:
                    obj.delete()
                errors['non_field_errors'] = ['An error occurred while %s.' % errored_action]

        return errors

    def retire(self, user, comment=None):
        if self.head.retired:
            return {'__all__': CONCEPT_IS_ALREADY_RETIRED}

        return self.__update_retire(True, comment or CONCEPT_WAS_RETIRED, user)

    def unretire(self, user, comment=None):
        if not self.head.retired:
            return {'__all__': CONCEPT_IS_ALREADY_NOT_RETIRED}

        return self.__update_retire(False, comment or CONCEPT_WAS_UNRETIRED, user)

    def __update_retire(self, retired, comment, user):
        latest_version = self.get_latest_version()
        new_version = latest_version.clone()
        new_version.retired = retired
        new_version.comment = comment
        return Concept.persist_clone(new_version, user)

    @staticmethod
    def get_unidirectional_mappings():  # make it use django relations related_names
        return []  # TODO: remove once mapping model is done
        # from core.mappings.models import Mapping
        # return Mapping.objects.filter(parent=self.parent, from_concept=self)
