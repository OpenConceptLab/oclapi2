from elasticsearch_dsl import Document

REPO_INDEXES = ['sources', 'collections']


class RepoDocument(Document):
    multi_index = True
    indexes = REPO_INDEXES

    class Meta:
        index = REPO_INDEXES

    @staticmethod
    def get_match_phrase_attrs():
        return ['name', 'external_id', 'canonical_url']

    @staticmethod
    def get_exact_match_attrs():
        return {
            'mnemonic': {
                'boost': 4,
            },
            'name': {
                'boost': 3.5,
            },
            'canonical_url': {
                'boost': 3,
            },
            'external_id': {
                'boost': 2.5
            }
        }

    @staticmethod
    def get_wildcard_search_attrs():
        return {
            'mnemonic': {
                'boost': 1,
                'lower': True,
                'wildcard': True
            },
            'name': {
                'boost': 0.8,
                'lower': True,
                'wildcard': True
            },
            'canonical_url': {
                'boost': 0.6,
                'lower': True,
                'wildcard': True
            }
        }

    @staticmethod
    def get_fuzzy_search_attrs():
        return {
            'name': {
                'boost': 0.8,
            },
        }
