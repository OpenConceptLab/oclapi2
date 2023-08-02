from unittest.mock import patch, Mock

from core.common.tests import OCLTestCase
from core.toggles.models import Toggle


class ToggleTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        Toggle(name='qa-active-default-true',  qa=True, is_active=True, dev=False).save()
        Toggle(name='qa-default-active-false', qa=False, dev=False).save()
        Toggle(name='qa-inactive-default-true', qa=True, is_active=False, dev=False).save()
        Toggle(name='qa-inactive-false', qa=True, is_active=False, dev=False).save()
        Toggle(name='dev').save()

    @patch('core.toggles.models.settings', Mock(ENV='qa'))
    def test_all(self):
        qa_toggles = Toggle.all()

        self.assertTrue(qa_toggles.count() >= 3)
        toggle_names = list(qa_toggles.values_list('name', flat=True).order_by('-id'))
        for toggle in ['qa-default-active-false', 'qa-active-default-true', 'dev']:
            self.assertTrue(toggle in toggle_names)

    @patch('core.toggles.models.settings', Mock(ENV='qa'))
    def test_get(self):
        self.assertTrue(Toggle.get('qa-active-default-true'))
        self.assertFalse(Toggle.get('qa-active-active-false'))
        self.assertIsNone(Toggle.get('qa-inactive-default-true'))
        self.assertIsNone(Toggle.get('qa-inactive-false'))

    @patch('core.toggles.models.settings', Mock(ENV='qa'))
    def test_to_dict(self):
        toggle_dict = Toggle.to_dict()

        self.assertTrue(toggle_dict['qa-active-default-true'])
        self.assertFalse(toggle_dict['qa-default-active-false'])
