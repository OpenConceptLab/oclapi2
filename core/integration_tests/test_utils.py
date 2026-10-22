import json
import os
from datetime import timedelta

from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from jsonpath_ng import parse

from core.collections.tests.factories import OrganizationCollectionFactory, ExpansionFactory
from core.sources.tests.factories import OrganizationSourceFactory


def load_json(file, parent_dir=None):
    if parent_dir:
        if parent_dir.startswith(os.path.pathsep):
            file_path = parent_dir
        else:
            module_dir = os.path.dirname(__file__)  # get current directory
            file_path = os.path.join(module_dir, parent_dir)
    else:
        file_path = os.path.dirname(__file__)
    file_path = os.path.join(file_path, file)
    with open(file_path) as f:
        json_file = json.load(f)
    return json_file


def update_json(json_input, path, value):
    jsonpath_expr = parse(path)
    jsonpath_expr.update_or_create(json_input, value)


def find(json_input, path):
    jsonpath_expr = parse(path)
    return jsonpath_expr.find(json_input)


def ignore_json_paths(self, json_input, json_response, paths):
    for path in paths:
        self.update(json_input, path, None)
        self.update(json_response, path, None)


class SourceChildListQueryCountMixin:
    """
    A page of concepts or mappings costs the same number of queries whatever its size, and serializes as row by row
    did. Set model, relation ('concepts' or 'mappings'), list_serializer and detail_serializer; setUp creates 4 rows
    in self.source and calls set_released_versions.
    """
    model = None
    relation = None
    list_serializer = None
    detail_serializer = None
    small_page_size = 2  # the smaller page must hold the same kinds of rows as the page of 4

    def get_page(self, url, limit, params=''):
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(f'{url}?limit={limit}{params}', format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), limit)
        return response, len(context.captured_queries)

    def assert_page(self, url, serializer_class, params=''):
        """Same query count for a small page and a page of 4; same output as serializing each row on its own."""
        _, small_page_queries = self.get_page(url, self.small_page_size, params)
        response, page_queries = self.get_page(url, 4, params)
        self.assertEqual(page_queries, small_page_queries)

        context = {'request': response.renderer_context['request'], 'view': response.renderer_context['view']}
        rows = [
            self.model.objects.get(versioned_object_id=row['versioned_object_id'], version=row['version'])
            for row in response.data
        ]
        self.assertEqual(response.data, [serializer_class(row, context=context).data for row in rows])
        return response.data, rows

    def set_released_versions(self, rows):
        """
        Newest first by updated_at in creation order, so a page of 2 and a page of 4 hold the same kinds of rows.
        v1 holds every latest version row, v2 every other one; both hold the newest head as well.
        """
        self.heads = list(rows.filter(id=F('versioned_object_id')).order_by('id'))
        now = timezone.now()
        for age, row in enumerate(rows.order_by('-id')):
            self.model.objects.filter(id=row.id).update(updated_at=now - timedelta(minutes=age))
        versions = list(rows.exclude(id=F('versioned_object_id')).filter(is_latest_version=True).order_by('id'))
        self.v1 = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='v1')
        self.v2 = OrganizationSourceFactory(
            mnemonic=self.source.mnemonic, organization=self.source.organization, version='v2')
        getattr(self.v1, self.relation).add(*versions, self.heads[-1])
        getattr(self.v2, self.relation).add(*versions[::2], self.heads[-1])
        self.in_v2 = {row.id for row in versions[::2]} | {self.heads[-1].id}

    def test_head_list(self):
        url = getattr(self.source, f'{self.relation}_url')
        data, rows = self.assert_page(url, self.list_serializer)
        self.assert_page(url, self.detail_serializer, '&verbose=true')

        for row, instance in zip(data, rows):
            self.assertEqual(row['version_url'], instance.get_latest_version().uri)
            self.assertEqual(row['latest_source_version'], 'v2' if instance.id == self.heads[-1].id else None)

    def test_version_list(self):
        url = getattr(self.v1, f'{self.relation}_url')
        data, rows = self.assert_page(url, self.list_serializer)
        self.assert_page(url, self.detail_serializer, '&verbose=true')

        for row, instance in zip(data, rows):
            self.assertEqual(row['latest_source_version'], 'v2' if instance.id in self.in_v2 else 'v1')

    def test_collection_list(self):
        """In a collection, rows show the source version their references resolved to (v1), not the latest one."""
        collection = OrganizationCollectionFactory()
        expansion = ExpansionFactory(collection_version=collection)
        collection.expansion_uri = expansion.uri
        collection.save()
        collection.add_expressions(
            {'expressions': [f'{self.v1.uri}{self.relation}/{head.mnemonic}/' for head in self.heads]},
            collection.created_by)

        url = getattr(collection, f'{self.relation}_url')
        data, _ = self.assert_page(url, self.model.get_serializer_class(version=True))
        self.assert_page(url, self.model.get_serializer_class(verbose=True, version=True), '&verbose=true')
        self.assertEqual({row['latest_source_version'] for row in data}, {'v1'})

    def test_global_list(self):
        self.assert_page(f'/{self.relation}/', self.list_serializer)
        self.assert_page(f'/{self.relation}/', self.detail_serializer, '&verbose=true')
