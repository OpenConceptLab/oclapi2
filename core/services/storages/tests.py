from unittest.mock import patch, Mock

from core.services.storages.postgres import PostgresQL
from core.common.tests import OCLTestCase


class PostgresQLTest(OCLTestCase):
    @patch('core.services.storages.postgres.connection')
    def test_create_seq(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock())
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.create_seq('foobar_seq', 'sources.uri', 1, 100), None)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with(
            'CREATE SEQUENCE IF NOT EXISTS foobar_seq MINVALUE 1 START 100 OWNED BY sources.uri;')

    @patch('core.services.storages.postgres.connection')
    def test_update_seq(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock())
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.update_seq('foobar_seq', 1567), None)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("SELECT setval('foobar_seq', 1567, true);")

    @patch('core.services.storages.postgres.connection')
    def test_drop_seq(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock())
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.drop_seq('foobar_seq'), None)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("DROP SEQUENCE IF EXISTS foobar_seq;")

    @patch('core.services.storages.postgres.connection')
    def test_next_value(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock(), fetchone=Mock(return_value=[1568]))
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.next_value('foobar_seq'), 1568)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("SELECT nextval('foobar_seq');")

    @patch('core.services.storages.postgres.connection')
    def test_last_value(self, db_connection_mock):
        cursor_context_mock = Mock(execute=Mock(), fetchone=Mock(return_value=[1567]))
        cursor_mock = Mock()
        cursor_mock.__enter__ = Mock(return_value=cursor_context_mock)
        cursor_mock.__exit__ = Mock(return_value=None)
        db_connection_mock.cursor = Mock(return_value=cursor_mock)

        self.assertEqual(PostgresQL.last_value('foobar_seq'), 1567)

        db_connection_mock.cursor.assert_called_once()
        cursor_context_mock.execute.assert_called_once_with("SELECT last_value from foobar_seq;")
