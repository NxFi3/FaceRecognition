"""Template localization regressions; routes/actions and tenant data stay real."""
import re
from datetime import timedelta
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import CompanyOwner, Device, Product, School


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ReleaseWebLocaleTests(TestCase):
    def setUp(self):
        product = Product.objects.get(pk='facerecognition')
        product.is_published = True
        product.save(update_fields=['is_published'])
        self.user = get_user_model().objects.create_user('synthetic-locale@example.invalid', email='synthetic-locale@example.invalid')
        self.school = School.objects.create(user=self.user, name='Synthetic locale school',
            status='active', student_limit=900, starts_at=timezone.now() - timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=365), email_verified_at=timezone.now())
        self.device = Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label='Synthetic locale device',
            password_stamp=self.user.get_session_auth_hash(), credential_expires_at=timezone.now() + timedelta(days=30))
        self.owner = get_user_model().objects.create_user('synthetic-owner-locale@example.invalid')
        CompanyOwner.objects.create(user=self.owner)

    def response(self, path, language='en'):
        return self.client.get(path, secure=True, HTTP_ACCEPT_LANGUAGE=language)

    def test_public_guidance_legal_and_contact_static_copy_is_bilingual(self):
        cases = {
            '/products/facerecognition/features/': ('From enrollment to reviewable reports.', 'از ثبت چهره، تا گزارشِ قابل بررسی.'),
            '/products/facerecognition/how-it-works/': ('Deployment in clear steps', 'استقرار، با قدم‌های روشن'),
            '/products/facerecognition/compare/': ('Manual attendance compared with FaceRecognition', 'مقایسهٔ ثبت دستی با گردش‌کار FaceRecognition'),
            '/products/facerecognition/requirements/': ('Hardware readiness is part of deployment.', 'آمادگی سخت‌افزار، بخشی از استقرار است.'),
            '/products/facerecognition/faq/': ('Frequently asked questions.', 'پرسش‌های متداول.'),
            '/support/': ('A backup you can recover.', 'بکاپی که بتوانید بازیابی کنید.'),
            '/privacy/': ('Cloud backup requires consent', 'بکاپ ابری فقط با رضایت'),
            '/terms/': ('Final contract review', 'بررسی قرارداد نهایی'),
            '/contact/': ('Tell us what you need.', 'دربارهٔ نیازتان بنویسید.'),
        }
        for path, (english, persian) in cases.items():
            with self.subTest(path=path, language='en'):
                response = self.response(path)
                self.assertContains(response, english)
                self.assertNotContains(response, persian)
                self.assertContains(response, 'lang="en" dir="ltr"')
            with self.subTest(path=path, language='fa'):
                response = self.response(path, 'fa')
                self.assertContains(response, persian)
                self.assertContains(response, 'lang="fa" dir="rtl"')

    def test_demo_translates_search_accessibility_and_synthetic_rows(self):
        path = '/products/facerecognition/demo/'
        english = self.response(path)
        self.assertContains(english, 'Sample student F')
        self.assertContains(english, 'Grade 12 — B')
        self.assertContains(english, 'Search samples, for example “A” or “Late”')
        persian = self.response(path, 'fa')
        self.assertContains(persian, 'دانش‌آموز نمونهٔ ج')
        self.assertContains(persian, 'دوازدهم ـ ب')
        for response in [english, persian]:
            # The fixed JS dataset's identifiers and event hooks are preserved.
            self.assertContains(response, 'data-demo-day')
            self.assertContains(response, 'value="monday"')
            self.assertContains(response, 'data-demo-search')
            self.assertEqual(response.content.count(b'data-demo-row'), 6)

    def test_school_navigation_and_post_actions_preserve_authorization_contract(self):
        self.client.force_login(self.user)
        english = self.response('/products/facerecognition/account/')
        self.assertContains(english, 'School backups')
        self.assertContains(english, 'Contract capacity')
        self.assertContains(english, 'name="csrfmiddlewaretoken"')
        self.assertContains(english, f'action="/products/facerecognition/account/devices/{self.device.pk}/revoke/"')
        self.assertContains(english, '/products/facerecognition/account/orders/')
        persian = self.response('/products/facerecognition/account/', 'fa')
        self.assertContains(persian, 'بکاپ‌های مدرسه')
        self.assertContains(persian, 'ظرفیت قرارداد')
        self.assertContains(self.response('/products/facerecognition/account/orders/'), 'No orders have been placed yet.')
        self.assertContains(self.response('/products/facerecognition/account/tickets/'), 'New request')

    def test_owner_static_copy_and_fallbacks_are_bilingual_without_granting_access(self):
        self.client.force_login(self.owner)
        english = self.response('/owner/')
        self.assertContains(english, 'Orders and payment review')
        self.assertContains(english, 'Plans and prices')
        self.assertContains(english, 'No product subscriptions have been recorded.')
        self.assertNotContains(english, 'طرح‌ها و قیمت‌ها')
        persian = self.response('/owner/', 'fa')
        self.assertContains(persian, 'طرح‌ها و قیمت‌ها')
        self.assertContains(persian, 'اشتراک محصولی ثبت نشده است.')
        self.client.force_login(self.user)
        self.assertEqual(self.response('/owner/').status_code, 403)
        self.assertEqual(self.response('/owner/', 'fa').status_code, 403)

    def test_operator_content_remains_escaped_and_untranslated(self):
        self.school.name = '<script>operator-copy</script>'
        self.school.save(update_fields=['name'])
        self.client.force_login(self.user)
        for language in ('en', 'fa'):
            response = self.response('/products/facerecognition/account/', language)
            self.assertContains(response, '&lt;script&gt;operator-copy&lt;/script&gt;')
            self.assertNotContains(response, '<script>operator-copy</script>')

    def test_portal_next_step_reflects_subscription_state_in_both_languages(self):
        self.client.force_login(self.user)
        path = '/products/facerecognition/account/'
        for language, active_copy in (
            ('en', 'Your subscription is active.'),
            ('fa', 'اشتراک شما فعال است.'),
        ):
            with self.subTest(language=language, status='active'):
                response = self.response(path, language)
                self.assertContains(response, active_copy)
                self.assertNotContains(response, '1. Choose a plan.')
        self.school.status = 'expired'
        self.school.save(update_fields=['status'])
        for language in ('en', 'fa'):
            with self.subTest(language=language, status='expired'):
                response = self.response(path, language)
                self.assertNotContains(response, 'Your subscription is active.')
                self.assertContains(response, 'Operational access is inactive.' if language == 'en'
                                    else 'اشتراک عملیاتی فعال نیست.')

    def test_no_raw_persian_static_template_copy_except_language_name(self):
        # This checks literal text, labels, include arguments and filter fallbacks.
        # Dynamic operator content and translated Persian catalogs are out of scope.
        for path in (settings.BASE_DIR / 'templates').rglob('*.html'):
            source = path.read_text(encoding='utf8')
            parts = re.split(r'({%.*?%}|{{.*?}}|<[^>]+>)', source, flags=re.S)
            for part in parts:
                if not re.search('[\u0600-\u06ff]', part):
                    continue
                if part.startswith('{%') and re.match(r'{%\s*(?:translate|trans)\b', part):
                    continue
                if part.strip() == 'فارسی':
                    continue
                with self.subTest(template=str(path), fragment=part[:80]):
                    self.fail('Raw Persian literal remains outside the translation catalog')
