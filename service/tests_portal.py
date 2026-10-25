"""Browser-facing commercial workflows, tenant boundaries and release safety.

All identities, orders and release bytes are synthetic. SMTP uses Django's test
backend; no successful external payment or signed Windows build is simulated.
"""
from datetime import timedelta
import hashlib
from pathlib import Path
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .commerce import create_order
from .models import School
from .commerce_models import Order, Plan, VerifiedPayment
from .portal_models import ContactRequest, Release, SupportTicket, TicketReply

PASSWORD = 'Synthetic-portal-password!438'


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class PortalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('portal-one@example.com', email='portal-one@example.com', password=PASSWORD)
        self.other = get_user_model().objects.create_user('portal-two@example.com', email='portal-two@example.com', password=PASSWORD)
        now = timezone.now()
        self.school = School.objects.create(user=self.user, name='Synthetic School One', status='active',
            student_limit=900, starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=365))
        self.other_school = School.objects.create(user=self.other, name='Synthetic School Two', status='active',
            student_limit=900, starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=365))
        self.plan = Plan.objects.create(slug='annual-browser', name='Synthetic Annual Plan', price_irr=90000000,
            duration_days=365, student_limit=900, device_limit=1, is_active=True, is_public=True)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.release_root = Path(self.directory.name) / 'releases'
        self.release_root.mkdir()
        setting = override_settings(RELEASE_ROOT=self.release_root)
        setting.enable()
        self.addCleanup(setting.disable)

    def login(self, user=None):
        self.client.force_login(user or self.user)

    def make_order(self, school=None):
        return create_order(school or self.school, self.plan.pk, uuid.uuid4())

    def make_release(self, **changes):
        data = b'Synthetic installer fixture, not a released Windows executable'
        path = self.release_root / 'FaceRecognition-test.zip'
        path.write_bytes(data)
        fields = {'version': 'test-only', 'artifact_name': path.name, 'sha256': hashlib.sha256(data).hexdigest(),
                  'size_bytes': len(data), 'notes': 'Synthetic release fixture', 'is_published': True}
        fields.update(changes)
        return Release.objects.create(**fields), data

    def test_public_pages_are_persian_rtl_accessible_and_have_seo_metadata(self):
        names = ('home', 'features', 'how-it-works', 'demo', 'pricing', 'compare', 'downloads',
                 'requirements', 'faq', 'contact', 'support', 'privacy', 'terms')
        for name in names:
            with self.subTest(page=name):
                response = self.client.get(reverse(name), secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'lang="fa"')
                self.assertContains(response, 'dir="rtl"')
                self.assertContains(response, '<main')
                self.assertContains(response, 'name="description"')
                self.assertContains(response, 'rel="canonical"')
                self.assertNotContains(response, self.school.name)
                self.assertNotContains(response, self.other_school.name)

    def test_sitemap_lists_public_routes_and_excludes_private_business_urls(self):
        response = self.client.get('/sitemap.xml', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('xml', response['Content-Type'])
        self.assertContains(response, 'https://testserver' + reverse('pricing'))
        for path in ('/owner/', '/account/', '/admin/', '/api/'):
            self.assertNotContains(response, '<loc>https://testserver' + path)
        robots = self.client.get('/robots.txt', secure=True)
        self.assertEqual(robots.status_code, 200)
        self.assertContains(robots, 'Disallow: /owner/')

    def test_contact_requires_explicit_consent_and_never_activates_a_school(self):
        payload = {'name': 'Synthetic requester', 'email': 'requester@example.com', 'school_name': 'Demo school',
                   'kind': 'demo', 'body': 'Please arrange a synthetic demonstration.'}
        before = School.objects.count()
        response = self.client.post(reverse('contact'), payload, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ContactRequest.objects.exists())
        payload['consent'] = 'on'
        response = self.client.post(reverse('contact'), payload, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactRequest.objects.count(), 1)
        self.assertTrue(ContactRequest.objects.get().consent)
        self.assertEqual(School.objects.count(), before)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_contact_escapes_script_content_when_later_rendered(self):
        self.client.post(reverse('contact'), {'name': '<script>alert(1)</script>', 'email': 'requester@example.com',
            'kind': 'contact', 'body': '<img src=x onerror=alert(1)>', 'consent': 'on'}, secure=True)
        self.assertEqual(ContactRequest.objects.count(), 1)
        public = self.client.get(reverse('contact'), secure=True)
        self.assertNotContains(public, '<script>alert(1)</script>')
        self.assertNotContains(public, '<img src=x onerror=alert(1)>')

    def test_public_pricing_never_exposes_unpublished_plans(self):
        Plan.objects.create(slug='private', name='PRIVATE-PLAN-DO-NOT-LIST', price_irr=100, is_active=True, is_public=False)
        response = self.client.get(reverse('pricing'), secure=True)
        self.assertContains(response, self.plan.name)
        self.assertNotContains(response, 'PRIVATE-PLAN-DO-NOT-LIST')

    def test_synthetic_demo_contains_no_customer_business_data(self):
        self.login()
        response = self.client.get(reverse('demo'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.user.email)
        self.assertNotContains(response, self.other.email)
        self.assertNotContains(response, self.school.name)
        self.assertNotContains(response, self.other_school.name)

    def test_profile_ignores_privilege_and_subscription_fields_from_browser(self):
        self.login()
        before_expiry = self.school.expires_at
        response = self.client.post(reverse('profile'), {'name': 'Changed school name', 'is_staff': 'on',
            'is_superuser': 'on', 'student_limit': 999999, 'status': 'active', 'expires_at': '2099-01-01'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.school.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.school.name, 'Changed school name')
        self.assertEqual(self.school.student_limit, 900)
        self.assertEqual(self.school.expires_at, before_expiry)
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.other_school.refresh_from_db()
        self.assertEqual(self.other_school.name, 'Synthetic School Two')

    def test_checkout_validates_price_on_server_and_repeated_request_has_one_order(self):
        self.login()
        expiry = self.school.expires_at
        data = {'idempotency_key': str(uuid.uuid4()), 'amount_irr': '1', 'student_limit': '1000000',
                'device_limit': '99', 'duration_days': '999', 'status': 'fulfilled'}
        for _ in range(2):
            response = self.client.post(reverse('create-order', args=[self.plan.pk]), data, secure=True)
            self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.filter(school=self.school).count(), 1)
        order = Order.objects.get(school=self.school)
        self.assertEqual(order.amount_irr, self.plan.price_irr)
        self.assertEqual(order.student_limit, self.plan.student_limit)
        self.assertEqual(order.device_limit, self.plan.device_limit)
        self.assertEqual(order.duration_days, 365)
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_browser_success_redirect_and_unacknowledged_claim_never_activate(self):
        self.login()
        order = self.make_order()
        expiry = self.school.expires_at
        self.assertEqual(self.client.get(reverse('create-order', args=[self.plan.pk]), secure=True).status_code, 405)
        url = reverse('checkout', args=[order.pk])
        self.assertEqual(self.client.get(url+'?payment=success&status=paid&amount_irr=1', secure=True).status_code, 200)
        self.assertEqual(self.client.post(url, {'reference': 'BANK-123456'}, secure=True).status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_customer_payment_claim_only_marks_pending_verification(self):
        self.login()
        order = self.make_order()
        expiry = self.school.expires_at
        response = self.client.post(reverse('checkout', args=[order.pk]),
            {'reference': 'BANK-123456', 'acknowledge': 'on', 'status': 'fulfilled', 'amount_irr': '1'}, secure=True)
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_other_school_cannot_read_invoice_checkout_or_submit_payment(self):
        order = self.make_order()
        self.login(self.other)
        for name in ('invoice', 'checkout'):
            self.assertEqual(self.client.get(reverse(name, args=[order.pk]), secure=True).status_code, 404)
        self.assertEqual(self.client.post(reverse('checkout', args=[order.pk]),
            {'reference': 'BANK-123456', 'acknowledge': 'on'}, secure=True).status_code, 404)
        response = self.client.get(reverse('orders'), secure=True)
        self.assertNotContains(response, order.invoice_number)
        self.assertNotContains(response, str(order.pk))
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_cancellation_requires_post_and_cannot_mutate_other_school_order(self):
        order = self.make_order()
        self.login()
        url = reverse('order-cancel', args=[order.pk])
        self.assertEqual(self.client.get(url, secure=True).status_code, 405)
        self.login(self.other)
        self.assertIn(self.client.post(url, secure=True).status_code, (403, 404))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.login()
        self.assertEqual(self.client.post(url, secure=True).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_ticket_and_business_export_are_strictly_tenant_scoped(self):
        own = SupportTicket.objects.create(school=self.school, subject='PRIVATE-TICKET-ONE', category='privacy', body='Private request one')
        other = SupportTicket.objects.create(school=self.other_school, subject='PRIVATE-TICKET-TWO', category='support', body='Private request two')
        self.login()
        response = self.client.get(reverse('tickets'), secure=True)
        self.assertContains(response, own.subject)
        self.assertNotContains(response, other.subject)
        self.assertEqual(self.client.get(reverse('ticket', args=[other.pk]), secure=True).status_code, 404)
        self.assertEqual(self.client.post(reverse('ticket', args=[other.pk]), {'body': 'Injected reply'}, secure=True).status_code, 404)
        self.assertFalse(TicketReply.objects.filter(ticket=other).exists())
        exported = self.client.get(reverse('business-export'), secure=True)
        self.assertEqual(exported.status_code, 200)
        self.assertContains(exported, self.school.name)
        self.assertContains(exported, own.subject)
        self.assertNotContains(exported, self.other_school.name)
        self.assertNotContains(exported, other.subject)

    def test_ticket_creation_cannot_choose_other_school(self):
        self.login()
        response = self.client.post(reverse('tickets'), {'subject': 'Synthetic support', 'category': 'renewal',
            'body': 'Please assist renewal.', 'school': str(self.other_school.pk), 'status': 'closed'}, secure=True)
        self.assertEqual(response.status_code, 302)
        item = SupportTicket.objects.get()
        self.assertEqual(item.school_id, self.school.pk)
        self.assertEqual(item.status, 'open')

    def test_school_and_arbitrary_superuser_cannot_access_owner_operations(self):
        order = self.make_order()
        privileged = get_user_model().objects.create_user('unassigned-superuser@example.com', password=PASSWORD, is_staff=True, is_superuser=True)
        for user in (self.user, privileged):
            self.login(user)
            for name, args in (('owner', []), ('owner-health', []), ('owner-payment', [order.pk]), ('owner-release', [])):
                with self.subTest(user=user.username, route=name):
                    self.assertEqual(self.client.get(reverse(name, args=args), secure=True).status_code, 403)
            self.assertEqual(self.client.post(reverse('owner-payment', args=[order.pk]),
                {'reference': 'BANK-123456', 'amount_irr': order.amount_irr, 'confirmed': 'on'}, secure=True).status_code, 403)
        self.assertFalse(VerifiedPayment.objects.exists())

    def owner_login(self, recent=False):
        import time
        from .owner_auth import STEP_UP_SESSION_KEY
        from .owner_models import CompanyOwner
        user = get_user_model().objects.create_user('designated-owner@example.com', password=PASSWORD, is_staff=True)
        role = CompanyOwner.objects.create(user=user)
        self.login(user)
        if recent:
            session = self.client.session
            session[STEP_UP_SESSION_KEY] = {'issued': time.time(), 'user': str(user.pk), 'revision': role.revision,
                                          'auth': user.get_session_auth_hash()}
            session.save()
        return user

    def test_owner_payment_requires_step_up_and_verified_amount(self):
        order = self.make_order()
        expiry = self.school.expires_at
        owner = self.owner_login()
        url = reverse('owner-payment', args=[order.pk])
        payload = {'reference': 'BANK-123456', 'amount_irr': order.amount_irr, 'confirmed': 'on'}
        response = self.client.post(url, payload, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/owner/reauth/', response['Location'])
        self.assertFalse(VerifiedPayment.objects.exists())
        self.assertEqual(self.client.get(reverse('owner'), secure=True).context['revenue'], 0)
        response = self.client.post(reverse('owner-reauth'), {'password': PASSWORD}, secure=True)
        self.assertEqual(response.status_code, 302)
        payload['amount_irr'] = 1
        self.assertEqual(self.client.post(url, payload, secure=True).status_code, 200)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        payload['amount_irr'] = order.amount_irr
        for _ in range(2):
            self.assertEqual(self.client.post(url, payload, secure=True).status_code, 302)
        order.refresh_from_db()
        self.school.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(VerifiedPayment.objects.count(), 1)
        self.assertEqual(self.school.expires_at, expiry+timedelta(days=365))
        self.assertEqual(VerifiedPayment.objects.get().verified_by_id, owner.pk)
        self.assertEqual(self.client.get(reverse('owner'), secure=True).context['revenue'], order.amount_irr)

    def test_owner_contact_view_escapes_untrusted_school_input(self):
        ContactRequest.objects.create(name='<script>alert(1)</script>', email='requester@example.com', kind='contact',
            body='<img src=x onerror=alert(1)>', consent=True)
        self.owner_login()
        response = self.client.get(reverse('owner'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
        self.assertContains(response, '&lt;script&gt;')

    def test_release_requires_school_auth_and_matches_verified_artifact(self):
        release, expected = self.make_release()
        url = reverse('release-download', args=[release.pk])
        self.assertEqual(self.client.get(url, secure=True).status_code, 302)
        self.login()
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Content-SHA256'], release.sha256)
        self.assertEqual(b''.join(response.streaming_content), expected)

    def test_release_refuses_unpublished_corrupt_missing_or_traversal_artifacts(self):
        release, _ = self.make_release(is_published=False)
        self.login()
        url = reverse('release-download', args=[release.pk])
        self.assertEqual(self.client.get(url, secure=True).status_code, 404)
        release.is_published = True
        release.save()
        (self.release_root / release.artifact_name).write_bytes(b'CORRUPTED')
        self.assertEqual(self.client.get(url, secure=True).status_code, 503)
        (self.release_root / release.artifact_name).unlink()
        self.assertEqual(self.client.get(url, secure=True).status_code, 503)
        outside = self.release_root.parent / 'private.zip'
        outside.write_bytes(b'PRIVATE-NEVER-DOWNLOAD')
        Release.objects.filter(pk=release.pk).update(artifact_name='../private.zip',
            size_bytes=outside.stat().st_size, sha256=hashlib.sha256(outside.read_bytes()).hexdigest())
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'PRIVATE-NEVER-DOWNLOAD', status_code=503)

    def test_release_refuses_symlinks_even_when_hash_and_size_match(self):
        release, expected = self.make_release()
        path = self.release_root / release.artifact_name
        path.unlink()
        outside = self.release_root.parent / 'private.zip'
        outside.write_bytes(expected)
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable')
        self.login()
        self.assertEqual(self.client.get(reverse('release-download', args=[release.pk]), secure=True).status_code, 503)

    def test_csrf_required_for_commercial_mutations(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        order = self.make_order()
        payload = {'idempotency_key': str(uuid.uuid4()), 'reference': 'BANK-123456', 'acknowledge': 'on',
                   'name': 'Changed', 'subject': 'Test', 'category': 'support', 'body': 'Test', 'consent': 'on'}
        for name, args in (('create-order', [self.plan.pk]), ('checkout', [order.pk]), ('order-cancel', [order.pk]),
                           ('profile', []), ('tickets', []), ('contact', [])):
            with self.subTest(route=name):
                self.assertEqual(browser.post(reverse(name, args=args), payload, secure=True).status_code, 403)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_complete_new_purchase_issues_correct_signed_device_license_only_after_verification(self):
        import base64
        import json
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        buyer = get_user_model().objects.create_user('new-buyer@example.com', email='new-buyer@example.com', password=PASSWORD)
        school = School.objects.create(user=buyer, name='New synthetic school', email_verified_at=timezone.now())
        nonce = 'n' * 43
        device = str(uuid.uuid4())
        credentials = {'email': buyer.username, 'password': PASSWORD, 'device_id': device, 'nonce': nonce, 'license_version': 2}
        signer = Ed25519PrivateKey.generate()
        private = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        with override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(private).decode()):
            self.assertEqual(self.client.post('/api/v1/login/', json.dumps(credentials), content_type='application/json', secure=True).status_code, 403)
            self.login(buyer)
            response = self.client.post(reverse('create-order', args=[self.plan.pk]), {'idempotency_key': str(uuid.uuid4())}, secure=True)
            self.assertEqual(response.status_code, 302)
            order = Order.objects.get(school=school)
            self.assertEqual(self.client.post(reverse('checkout', args=[order.pk]),
                {'reference': 'BANK-NEW-PURCHASE', 'acknowledge': 'on'}, secure=True).status_code, 302)
            school.refresh_from_db()
            self.assertFalse(school.entitled)
            self.owner_login(recent=True)
            self.assertEqual(self.client.post(reverse('owner-payment', args=[order.pk]),
                {'reference': 'BANK-NEW-PURCHASE', 'amount_irr': self.plan.price_irr, 'confirmed': 'on'}, secure=True).status_code, 302)
            response = self.client.post('/api/v1/login/', json.dumps(credentials), content_type='application/json', secure=True)
            self.assertEqual(response.status_code, 200)
            envelope = response.json()['lease']
            raw = base64.b64decode(envelope['payload'])
            signer.public_key().verify(base64.b64decode(envelope['signature']), raw)
            lease = json.loads(raw)
            school.refresh_from_db()
            self.assertTrue(school.entitled)
            self.assertEqual(lease['status'], 'active')
            self.assertEqual(lease['school_id'], str(school.pk))
            self.assertEqual(lease['device_id'], device)
            self.assertEqual(lease['student_limit'], 900)
            self.assertEqual(lease['device_limit'], 1)
            self.assertEqual(lease['subscription_expires_at'], school.expires_at.timestamp())
            self.assertEqual(lease['nonce'], nonce)

    def test_business_export_rate_limit_preserves_authorized_data_boundary(self):
        self.login()
        for _ in range(4):
            response = self.client.get(reverse('business-export'), secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, self.other_school.name)
        response = self.client.get(reverse('business-export'), secure=True)
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    def test_payment_rejection_requires_explicit_owner_fresh_auth_and_post(self):
        order = self.make_order()
        url = reverse('owner-reject-payment', args=[order.pk])
        for user in (self.user, self.other):
            self.login(user)
            self.assertEqual(self.client.post(url, {'reason': 'Bank settlement not found.'}, secure=True).status_code, 403)
        self.owner_login()
        response = self.client.post(url, {'reason': 'Bank settlement not found.'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/owner/reauth/', response['Location'])
        self.assertEqual(self.client.post(reverse('owner-reauth'), {'password': PASSWORD}, secure=True).status_code, 302)
        self.assertEqual(self.client.get(url, secure=True).status_code, 405)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_rejected_claim_preserves_entitlement_and_displays_reason_without_verifying_payment(self):
        self.login()
        order = self.make_order()
        expiry = self.school.expires_at
        self.assertEqual(self.client.post(reverse('checkout', args=[order.pk]),
            {'reference': 'BANK-NOT-SETTLED', 'acknowledge': 'on'}, secure=True).status_code, 302)
        self.owner_login(recent=True)
        reason = 'Independent bank review found no settled payment.'
        url = reverse('owner-reject-payment', args=[order.pk])
        for _ in range(2):
            self.assertEqual(self.client.post(url, {'reason': reason}, secure=True).status_code, 302)
        order.refresh_from_db()
        self.school.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REJECTED)
        self.assertEqual(order.rejection_reason, reason)
        self.assertEqual(self.school.expires_at, expiry)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.assertEqual(self.client.get(reverse('owner'), secure=True).context['revenue'], 0)
        self.login()
        self.assertContains(self.client.get(reverse('checkout', args=[order.pk]), secure=True), reason)

    def test_payment_rejection_validates_reason_and_requires_csrf(self):
        order = self.make_order()
        owner = self.owner_login(recent=True)
        url = reverse('owner-reject-payment', args=[order.pk])
        self.assertEqual(self.client.post(url, {'reason': '   '}, secure=True).status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(order.rejection_reason, '')
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(owner)
        self.assertEqual(strict.post(url, {'reason': 'Bank settlement not found.'}, secure=True).status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertFalse(VerifiedPayment.objects.exists())
