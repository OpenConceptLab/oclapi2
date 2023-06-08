from elasticsearch_dsl import FacetedSearch


class CommonSearch(FacetedSearch):
    def __init__(self, query=None, filters={}, sort=(), exact_match=False):  # pylint: disable=dangerous-default-value
        self.exact_match = exact_match
        super().__init__(query=query, filters=filters, sort=sort)

    def format_search_str(self, search_str):
        if self.exact_match:
            return search_str.replace('*', '')
        return f"*{search_str}*".replace('**', '*')

    def query(self, search, query):
        if query:
            search_str = self.format_search_str(query)
            if self.fields:
                return search.filter('query_string', fields=self.fields, query=search_str)

            return search.query('multi_match', query=search_str)

        return search

    def params(self, **kwargs):
        self._s = self._s.params(**kwargs)
