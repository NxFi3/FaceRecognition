from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BrandedAccessTests(TestCase):
    def test_nonowner_denial_preserves_403_and_accessible_persian_rtl_document(self):
        user = get_user_model().objects.create_user('synthetic-denial@example.invalid')
        self.client.force_login(user)
        response = self.client.get('/owner/', secure=True, HTTP_ACCEPT_LANGUAGE='fa')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'lang="fa"', status_code=403)
        self.assertContains(response, 'dir="rtl"', status_code=403)
        self.assertContains(response, 'فقط برای مالک', status_code=403)
