"""Company-first routes and first-product isolation; all records are synthetic."""
from datetime import timedelta
from html.parser import HTMLParser
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .commerce import create_order
from .commerce_models import Product, ProductSubscription, Plan, Order
from .models import School
from .portal_models import Release, SupportTicket, TicketReply


class HeadingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_heading = False
        self.headings = []
    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.in_heading = True
    def handle_endtag(self, tag):
        if tag == 'h1':
            self.in_heading = False
    def handle_data(self, data):
        if self.in_heading:
            self.headings.append(data)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CompanyProductPortalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.face = Product.objects.get(pk='facerecognition')
        self.other_product = Product.objects.create(slug='private-unlaunched', name='UNLAUNCHED-PRODUCT-NOT-FOR-SALE',
            summary='Unlaunched internal fixture', is_published=True)
        self.user = get_user_model().objects.create_user('product-one@example.com', email='product-one@example.com')
        self.other_user = get_user_model().objects.create_user('product-two@example.com', email='product-two@example.com')
        now = timezone.now()
        self.school = School.objects.create(user=self.user, name='PRIVATE-SCHOOL-ONE', status='active', student_limit=900,
            starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=365))
        self.other_school = School.objects.create(user=self.other_user, name='PRIVATE-SCHOOL-TWO', status='active', student_limit=900,
            starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=365))
        self.plan = Plan.objects.create(product=self.face, slug='face-product-qa', name='Synthetic Face Plan',
            price_irr=90000000, duration_days=365, student_limit=900, device_limit=1, is_active=True, is_public=True)
        self.unsupported_plan = Plan.objects.create(product=self.other_product, slug='unlaunched-qa', name='UNLAUNCHED-PLAN-NOT-FOR-SALE',
            price_irr=100, duration_days=365, student_limit=1, device_limit=1, is_active=True, is_public=True)
    def login(self):
        self.client.force_login(self.user)
    def heading(self, response):
        parser = HeadingParser()
        parser.feed(response.content.decode())
        return ' '.join(parser.headings)
    def test_home_introduces_company_and_is_distinct_from_first_product_landing(self):
        company = self.client.get(reverse('home'), secure=True, HTTP_ACCEPT_LANGUAGE='fa')
        product = self.client.get(reverse('product-facerecognition'), secure=True)
        self.assertEqual(company.status_code, 200)
        self.assertEqual(product.status_code, 200)
        self.assertIn('NxFi Technologies', self.heading(company))
        self.assertContains(company, 'هوش مصنوعی')
        self.assertNotEqual(self.heading(company), self.heading(product))
        self.assertContains(company, reverse('products'))
        self.assertContains(company, reverse('product-facerecognition'))
        self.assertNotContains(company, self.school.name)
    def test_products_catalog_never_advertises_unlaunched_product_even_if_marked_published(self):
        for name in ('home', 'products'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name), secure=True)
                self.assertContains(response, 'FaceRecognition')
                self.assertNotContains(response, self.other_product.name)
                self.assertNotContains(response, 'CarpetVision')
                self.assertNotContains(response, 'Cloud AI')
    def test_complete_product_routes_are_canonical_and_have_product_metadata(self):
        self.assertEqual(reverse('product-facerecognition'), '/products/facerecognition/')
        for name in ('features','how-it-works','demo','pricing','compare','downloads','requirements','faq'):
            with self.subTest(page=name):
                url = reverse(name)
                self.assertEqual(url, '/products/facerecognition/'+name+'/')
                response = self.client.get(url, secure=True, HTTP_ACCEPT_LANGUAGE="fa")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'lang="fa"')
                self.assertContains(response, 'dir="rtl"')
                self.assertContains(response, 'https://testserver'+url)
                self.assertContains(response, reverse('product-facerecognition'))
    def test_sitemap_lists_company_catalog_and_product_routes_not_legacy_or_private_urls(self):
        response = self.client.get(reverse('sitemap'), secure=True)
        for name in ('home','products','product-facerecognition','features','pricing','downloads'):
            self.assertContains(response, '<loc>https://testserver'+reverse(name)+'</loc>')
        for path in ('/features/','/pricing/','/downloads/','/account/','/products/facerecognition/account/','/owner/'):
            self.assertNotContains(response, '<loc>https://testserver'+path+'</loc>')
    def test_legacy_public_get_redirects_preserve_query_string(self):
        for name in ('features','how-it-works','demo','pricing','compare','downloads','requirements','faq'):
            with self.subTest(page=name):
                response = self.client.get('/'+name+'/?source=old-link&next=%2Ftest%2F', secure=True)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], reverse(name)+'?source=old-link&next=%2Ftest%2F')
    def test_company_workspace_and_face_portal_are_different_owned_views(self):
        ProductSubscription.objects.create(school=self.school, product=self.face, status='active',
            starts_at=self.school.starts_at, expires_at=self.school.expires_at, student_limit=900, device_limit=1)
        ProductSubscription.objects.create(school=self.other_school, product=self.face, status='active',
            starts_at=self.other_school.starts_at, expires_at=self.other_school.expires_at, student_limit=900, device_limit=1)
        self.login()
        self.assertEqual(reverse('workspace'), '/account/')
        self.assertEqual(reverse('account'), '/products/facerecognition/account/')
        workspace = self.client.get(reverse('workspace'), secure=True)
        portal = self.client.get(reverse('account'), secure=True)
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(portal.status_code, 200)
        self.assertContains(workspace, 'FaceRecognition')
        self.assertContains(workspace, reverse('account'))
        self.assertNotContains(workspace, self.other_school.name)
        self.assertContains(portal, self.school.name)
        self.assertNotContains(portal, self.other_school.name)
    def test_face_pricing_filters_other_product_and_browser_product_tampering(self):
        response = self.client.get(reverse('pricing'), secure=True)
        self.assertContains(response, self.plan.name)
        self.assertNotContains(response, self.unsupported_plan.name)
        self.login()
        response = self.client.post(reverse('create-order', args=[self.plan.pk]),
            {'idempotency_key': str(uuid.uuid4()), 'product': self.other_product.pk, 'amount_irr': 1}, secure=True)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.product_id, self.face.pk)
        self.assertEqual(order.amount_irr, self.plan.price_irr)
        self.assertEqual(order.product_name, self.face.name)
    def test_unsupported_plan_cannot_be_purchased_through_face_checkout(self):
        self.login()
        expiry = self.school.expires_at
        response = self.client.post(reverse('create-order', args=[self.unsupported_plan.pk]),
            {'idempotency_key': str(uuid.uuid4())}, secure=True)
        self.assertIn(response.status_code, (302, 403, 404))
        self.assertFalse(Order.objects.exists())
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        self.assertFalse(ProductSubscription.objects.filter(school=self.school, product=self.other_product).exists())
    def test_legacy_post_checkout_is_processed_without_losing_request_body(self):
        self.login()
        key = str(uuid.uuid4())
        response = self.client.post('/account/orders/new/'+str(self.plan.pk)+'/', {'idempotency_key': key}, secure=True)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.product_id, self.face.pk)
        self.assertEqual(response['Location'], reverse('checkout', args=[order.pk]))
        response = self.client.post('/account/orders/'+str(order.pk)+'/',
            {'reference': 'BANK-SYNTHETIC-443311', 'acknowledge': 'on'}, secure=True)
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)
        self.assertEqual(response['Location'], reverse('checkout', args=[order.pk]))
    def test_downloads_are_first_product_scoped(self):
        Release.objects.create(product=self.other_product, version='PRIVATE-OTHER-PRODUCT-RELEASE', artifact_name='private-other.zip',
            sha256='a'*64, size_bytes=1, notes='Private other product fixture', is_published=True)
        response = self.client.get(reverse('downloads'), secure=True)
        self.assertNotContains(response, 'PRIVATE-OTHER-PRODUCT-RELEASE')
        self.login()
        item = Release.objects.get()
        self.assertEqual(self.client.get(reverse('release-download', args=[item.pk]), secure=True).status_code, 404)
    def test_face_support_and_exports_do_not_include_other_product_or_other_school_records(self):
        own = SupportTicket.objects.create(school=self.school, product=self.face, subject='FACE-OWN-TICKET', category='support', body='Synthetic Face request')
        hidden = SupportTicket.objects.create(school=self.school, product=self.other_product, subject='OTHER-PRODUCT-SECRET', category='support', body='Unlaunched fixture')
        other = SupportTicket.objects.create(school=self.other_school, product=self.face, subject='OTHER-SCHOOL-SECRET', category='support', body='Other school fixture')
        self.login()
        listing = self.client.get(reverse('tickets'), secure=True)
        self.assertContains(listing, own.subject)
        self.assertNotContains(listing, hidden.subject)
        self.assertNotContains(listing, other.subject)
        self.assertEqual(self.client.get(reverse('ticket', args=[hidden.pk]), secure=True).status_code, 404)
        exported = self.client.get(reverse('business-export'), secure=True)
        self.assertContains(exported, own.subject)
        self.assertNotContains(exported, hidden.subject)
        self.assertNotContains(exported, other.subject)
    def test_face_ticket_form_ignores_customer_product_and_tenant_fields(self):
        self.login()
        response = self.client.post(reverse('tickets'), {'subject': 'Synthetic Face support', 'category': 'support',
            'body': 'Test product binding', 'product': self.other_product.pk, 'school': self.other_school.pk}, secure=True)
        self.assertEqual(response.status_code, 302)
        item = SupportTicket.objects.get()
        self.assertEqual(item.school_id, self.school.pk)
        self.assertEqual(item.product_id, self.face.pk)


# Exercise a real upgrade of synthetic legacy rows, rather than only testing
# newly-created records with current defaults. No production database is used.
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyProductMigrationTests(TransactionTestCase):
    reset_sequences = True
    def test_legacy_school_orders_releases_and_support_bind_to_face_without_data_loss(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes('service')
        previous = [('service', '0004_order_rejection_reason_alter_order_status')]
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(previous)
        old = executor.loader.project_state(previous).apps
        User = old.get_model('auth', 'User')
        SchoolModel = old.get_model('service', 'School')
        PlanModel = old.get_model('service', 'Plan')
        OrderModel = old.get_model('service', 'Order')
        DeviceModel = old.get_model('service', 'Device')
        ReleaseModel = old.get_model('service', 'Release')
        TicketModel = old.get_model('service', 'SupportTicket')
        now = timezone.now()
        start, end = now-timedelta(days=20), now+timedelta(days=345)
        user = User.objects.create(username='migration-synthetic@example.com', email='migration-synthetic@example.com', is_active=True)
        school = SchoolModel.objects.create(user_id=user.pk, name='Legacy synthetic school', status='active',
            starts_at=start, expires_at=end, student_limit=923, device_limit=2)
        banned_user = User.objects.create(username='banned-migration@example.com', is_active=True)
        banned = SchoolModel.objects.create(user_id=banned_user.pk, name='Legacy banned fixture', status='banned',
            starts_at=start, expires_at=end, student_limit=777, device_limit=1)
        plan = PlanModel.objects.create(slug='legacy-plan', name='Legacy annual fixture', price_irr=88990000,
            duration_days=365, student_limit=923, device_limit=2, is_active=True, is_public=True)
        order = OrderModel.objects.create(school_id=school.pk, plan_id=plan.pk, purpose='renewal', status='fulfilled',
            plan_name=plan.name, school_name=school.name, billing_email=user.email, amount_irr=88990000,
            duration_days=365, student_limit=923, device_limit=2, is_trial=False, expires_at=now+timedelta(days=7),
            fulfilled_at=now, subscription_starts_at=start, subscription_expires_at=end)
        device = DeviceModel.objects.create(school_id=school.pk, installation_id=uuid.uuid4(), label='Legacy fixture',
            token_digest='a'*64, password_stamp='b'*64, credential_expires_at=end, revoked=False)
        release = ReleaseModel.objects.create(version='Legacy fixture release', artifact_name='legacy-fixture.zip',
            sha256='c'*64, size_bytes=15, notes='Synthetic legacy metadata only', is_published=False)
        ticket = TicketModel.objects.create(school_id=school.pk, subject='Legacy support', category='support', body='Synthetic legacy support')
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        new = executor.loader.project_state(latest).apps
        ProductModel = new.get_model('service', 'Product')
        SubscriptionModel = new.get_model('service', 'ProductSubscription')
        current_school = new.get_model('service', 'School').objects.get(pk=school.pk)
        self.assertEqual((current_school.status,current_school.starts_at,current_school.expires_at,current_school.student_limit,current_school.device_limit),
                         ('active',start,end,923,2))
        self.assertEqual(ProductModel.objects.get(pk='facerecognition').name, 'FaceRecognition')
        current_order = new.get_model('service', 'Order').objects.get(pk=order.pk)
        self.assertEqual((current_order.product_id,current_order.product_name,current_order.amount_irr,current_order.status),
                         ('facerecognition','FaceRecognition',88990000,'fulfilled'))
        self.assertEqual(new.get_model('service', 'Plan').objects.get(pk=plan.pk).product_id, 'facerecognition')
        self.assertEqual(new.get_model('service', 'Release').objects.get(pk=release.pk).product_id, 'facerecognition')
        self.assertEqual(new.get_model('service', 'SupportTicket').objects.get(pk=ticket.pk).product_id, 'facerecognition')
        current_device = new.get_model('service', 'Device').objects.get(pk=device.pk)
        self.assertEqual((current_device.token_digest,current_device.password_stamp,current_device.installation_id),
                         ('a'*64,'b'*64,device.installation_id))
        subscription = SubscriptionModel.objects.get(school_id=school.pk, product_id='facerecognition')
        self.assertEqual((subscription.status,subscription.starts_at,subscription.expires_at,subscription.student_limit,subscription.device_limit),
                         ('active',start,end,923,2))
        self.assertEqual(SubscriptionModel.objects.get(school_id=banned.pk, product_id='facerecognition').status, 'banned')
        self.assertEqual(new.get_model('service','School').objects.get(pk=banned.pk).status, 'banned')
