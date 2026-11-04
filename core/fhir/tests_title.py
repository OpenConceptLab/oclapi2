from django.test import tag
from core.common.tests import OCLTestCase
from core.fhir.tests_search import SearchCorpusMixin, collection, source
from core.orgs.tests.factories import OrganizationFactory

TITLED = 'https://example.org/titled'
BARE = 'https://example.org/bare'
BLANK = 'https://example.org/blank'


@tag('es')
class FhirTitleTest(SearchCorpusMixin, OCLTestCase):
    """The FHIR title is the repository's full name, or its name when it has none (empty or blank)."""
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.titles = OrganizationFactory(mnemonic='titles')
        for make, kind in ((source, 'cs'), (collection, 'vs')):
            for mnemonic, full_name, url in (('titled', 'The Full Name', TITLED), ('bare', '', BARE),
                                              ('blank', ' \t ', BLANK), ('nbsp', '\u00a0', f'{BLANK}/nbsp'),
                                              ('figure-space', '\u2007', f'{BLANK}/figure-space')):
                make(cls.titles, f'{kind}-{mnemonic}', released=True, canonical_url=f'{url}/{kind}',
                     full_name=full_name)
                make(cls.titles, f'{kind}-{mnemonic}', 'v1', released=True, canonical_url=f'{url}/{kind}',
                     full_name=full_name)

    def expected(self, kind):
        return {
            f'{kind}-titled': 'The Full Name', f'{kind}-bare': f'{kind}-bare', f'{kind}-blank': f'{kind}-blank',
            f'{kind}-nbsp': f'{kind}-nbsp', f'{kind}-figure-space': f'{kind}-figure-space'}

    def test_search_entries_and_reads_at_both_bases(self):
        for resource_type, kind in (('CodeSystem', 'cs'), ('ValueSet', 'vs')):
            for base in ('/fhir', '/orgs/titles'):
                bundle = self.search(resource_type, base=base)
                titles = {entry['resource']['name']: entry['resource'].get('title') for entry in bundle['entry']
                          if entry['resource']['name'].startswith(f'{kind}-')}
                self.assertEqual(titles, self.expected(kind), (resource_type, base))
            for mnemonic, title in self.expected(kind).items():
                read = self.read(f'/orgs/titles/{resource_type}/{mnemonic}/', version='v1')
                self.assertEqual(read['title'], title, (resource_type, mnemonic))
                found = self.search(resource_type, base='/fhir', **{'name:exact': mnemonic})
                global_id = found['entry'][0]['resource']['id']
                self.assertEqual(self.read(f'/fhir/{resource_type}/{global_id}/')['title'], title)

    def names_found(self, where, key, value):
        resource_type, base, kind = where
        bundle = self.search(resource_type, base=base, **{key: value})
        names = {entry['resource']['name'] for entry in bundle.get('entry', [])}
        return sorted(name for name in names if name.startswith(f'{kind}-'))

    def test_title_search_matches_the_title_the_read_shows(self):
        for resource_type, kind in (('CodeSystem', 'cs'), ('ValueSet', 'vs')):
            for base in ('/fhir', '/orgs/titles'):
                def check(expected, key, value, where=(resource_type, base, kind)):
                    self.assertEqual(
                        self.names_found(where, key, value), [f'{where[2]}-{n}' for n in expected], (where, key, value))

                check(['bare'], 'title:exact', f'{kind}-bare')
                check(['blank'], 'title:exact', f'{kind}-blank')
                check(['titled'], 'title:exact', 'The Full Name')
                check(['bare'], 'title', f'{kind}-ba')
                check(['titled'], 'title', 'the full')
                check(['blank'], 'title:contains', 'BLAN')
                check(['titled'], 'title:contains', 'full')
                for mnemonic in ('nbsp', 'figure-space'):
                    check([mnemonic], 'title:exact', f'{kind}-{mnemonic}')
                    check([mnemonic], 'title', f'{kind}-{mnemonic[:-1]}')
                    check([mnemonic], 'title:contains', mnemonic.upper())
                # a name doesn't match the title when there is a full name
                check([], 'title:exact', f'{kind}-titled')
                check([], 'title:contains', 'titled')
