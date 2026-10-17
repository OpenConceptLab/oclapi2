from core.common.tests import OCLTestCase
from core.fhir.tests_search import SearchCorpusMixin, collection, source
from core.orgs.tests.factories import OrganizationFactory

TITLED = 'https://example.org/titled'
BARE = 'https://example.org/bare'
BLANK = 'https://example.org/blank'


class FhirTitleTest(SearchCorpusMixin, OCLTestCase):
    """The FHIR title is the repository's full name, or its name when it has none (empty or blank)."""
    def setUp(self):
        super().setUp()
        self.titles = OrganizationFactory(mnemonic='titles')
        for make, kind in ((source, 'cs'), (collection, 'vs')):
            for mnemonic, full_name, url in (('titled', 'The Full Name', TITLED), ('bare', '', BARE),
                                              ('blank', ' \t ', BLANK)):
                make(self.titles, f'{kind}-{mnemonic}', released=True, canonical_url=f'{url}/{kind}',
                     full_name=full_name)
                make(self.titles, f'{kind}-{mnemonic}', 'v1', released=True, canonical_url=f'{url}/{kind}',
                     full_name=full_name)

    def expected(self, kind):
        return {f'{kind}-titled': 'The Full Name', f'{kind}-bare': f'{kind}-bare', f'{kind}-blank': f'{kind}-blank'}

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
