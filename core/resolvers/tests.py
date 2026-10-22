from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from core.common.tests import OCLAPITestCase
from core.concepts.tests.factories import ConceptFactory, ConceptNameFactory
from core.resolvers.core import validate_resolvers
from core.resolvers.names import NamesResolver
from core.sources.tests.factories import OrganizationSourceFactory

# ICD-11 use case: '&' attaches an extension code in brackets and '/' joins clusters. Also used by the API tests.
RESOLVERS = {'names': {'replace': [
    {'pattern': '&', 'substitution': ' [{{name}}]'}, {'pattern': '/', 'substitution': ' / '}]}}
CONCEPTS = {
    'ND13.2': 'Fracture of unspecified tarsal bone', 'ND13.3': 'Fracture of metatarsal bone',
    'XJ44E': 'Closed fracture',
}

# (resolvers, concepts of the Source, code, expected name): one resolver, one configuration per use case
COMPOSITION_CASES = (
    (RESOLVERS, CONCEPTS, 'ND13.2&XJ44E/ND13.3&XJ44E',
     'Fracture of unspecified tarsal bone [Closed fracture] / Fracture of metatarsal bone [Closed fracture]'),
    (  # SNOMED CT: the attribute is a code too; '{' and '}' only group attributes
        {'names': {'replace': [
            {'pattern': ':', 'substitution': ' with '}, {'pattern': '{', 'substitution': ''},
            {'pattern': '}', 'substitution': ''}, {'pattern': ',', 'substitution': ' and '},
            {'pattern': '=', 'substitution': ': '}]}},
        {'71388002': 'Procedure', '260686004': 'Method', '129304002': 'Excision - action',
         '405813007': 'Procedure site - Direct', '15497006': 'Ovarian structure'},
        '71388002:{260686004=129304002,405813007=15497006}',
        'Procedure with Method: Excision - action and Procedure site - Direct: Ovarian structure'),
    (  # LOINC: the axis in braces is a label, copied without the braces and never looked up
        {'names': {'literals': [{'start': '{', 'end': '}'}], 'replace': [
            {'pattern': ':', 'substitution': ', '}, {'pattern': '=', 'substitution': ': '},
            {'pattern': ',', 'substitution': ', '}]}},
        {'104765-3': 'Neisseria gonorrhoeae DNA [Presence] in Specimen by Molecular genetics method',
         'LP7681-2': 'Specimen part', 'LP6464-4': 'Probe.amp.tar'},
        '104765-3:{Specimen}=LP7681-2,{Molgen}=LP6464-4',
        'Neisseria gonorrhoeae DNA [Presence] in Specimen by Molecular genetics method, Specimen: Specimen part, '
        'Molgen: Probe.amp.tar'),
)


def make_concept(source, code, name):
    """Creates a concept of the source with a single preferred English name."""
    concept = ConceptFactory(mnemonic=code, parent=source)
    ConceptNameFactory(concept=concept, name=name, locale='en', locale_preferred=True)
    return concept


def make_source(resolvers, concepts, **kwargs):
    """A Source with the given resolvers holding the given concepts ({code: name})."""
    source = OrganizationSourceFactory(resolvers=resolvers, **kwargs)
    for code, name in concepts.items():
        make_concept(source, code, name)
    return source


class NamesResolverTest(OCLAPITestCase):
    @staticmethod
    def resolve(source, *codes):
        return NamesResolver(source, AnonymousUser()).resolve(list(codes))

    def test_code_is_resolved_with_the_resolvers_of_the_source(self):
        for resolvers, concepts, code, name in COMPOSITION_CASES:
            with self.subTest(code=code):
                source = make_source(resolvers, concepts)
                simple_code = next(iter(concepts))
                self.assertEqual(self.resolve(source, code, simple_code), [name, concepts[simple_code]])

    def test_a_missing_part_gives_no_name(self):
        source = make_source(RESOLVERS, CONCEPTS)
        self.assertEqual(self.resolve(source, 'ND13.2&NOPE', 'ND13.2/', 'ND13.2&&XJ44E'), [None, None, None])

    def test_an_existing_concept_wins_and_without_resolvers_nothing_is_split(self):
        source = make_source(RESOLVERS, {**CONCEPTS, 'ND13.2%26XJ44E': 'Stored name'})
        self.assertEqual(self.resolve(source, 'ND13.2&XJ44E'), ['Stored name'])

        source = make_source(None, CONCEPTS)
        self.assertEqual(
            self.resolve(source, 'ND13.2&XJ44E', 'ND13.2'), [None, 'Fracture of unspecified tarsal bone'])

    def test_validate_resolvers(self):
        for resolvers, *_ in COMPOSITION_CASES:
            validate_resolvers(resolvers)
        for invalid in (
                'text', {'concepts': {}}, {'names': {}}, {'names': {'replace': []}},
                {'names': {'replace': [{'pattern': '&', 'substitution': '{{name}}{{name}}'}]}},
                {'names': {'replace': [{'pattern': '{', 'substitution': ''}],
                           'literals': [{'start': '{', 'end': '}'}]}}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError) as context:
                    validate_resolvers(invalid)
                self.assertIn('resolvers', context.exception.message_dict)
