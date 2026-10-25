"""Product isolation and legacy Face entitlement compatibility; no future SKU launched."""
from datetime import timedelta
import uuid

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.db import transaction, IntegrityError
from django.utils import timezone

from .commerce import (CommerceError, create_order, verify_manual_payment,
                       sync_face_subscription, supported_product_slugs)
from .commerce_models import Product, ProductSubscription, Plan, Order, VerifiedPayment, FACE_PRODUCT_SLUG
from .models import School
from .owner_models import CompanyOwner


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProductCommerceTests(TestCase):
    def setUp(self):
        self.face = Product.objects.get(pk=FACE_PRODUCT_SLUG)
        self.owner = get_user_model().objects.create_user('product-owner@example.com')
        CompanyOwner.objects.create(user=self.owner)
        user = get_user_model().objects.create_user('product-school@example.com')
        self.school = School.objects.create(user=user, name='Synthetic school')
        self.plan = Plan.objects.create(slug='face-first', name='First plan', price_irr=90000000,
            duration_days=365, student_limit=900, device_limit=1, is_active=True, is_public=True)
        # Test-only unpublished SKU, never seeded or advertised by production code.
        self.other = Product.objects.create(slug='test-only-unimplemented', name='Unimplemented test SKU',
            summary='Test fixture, not a launched product', is_published=False)
    def order(self, **kwargs):
        return create_order(self.school, self.plan.pk, kwargs.get('key', uuid.uuid4()))
    def pay(self, order):
        return verify_manual_payment(order.pk, self.owner, 'BANK-PRODUCT-123', order.amount_irr)
    def test_existing_face_plan_defaults_and_orders_are_product_bound(self):
        self.assertEqual(self.plan.product_id, FACE_PRODUCT_SLUG)
        order = self.order()
        self.assertEqual(order.product_id, FACE_PRODUCT_SLUG)
        self.assertEqual(order.product_name, 'FaceRecognition')
        self.face.name = 'Renamed catalog label'
        self.face.save()
        order.refresh_from_db()
        self.assertEqual(order.product_name, 'FaceRecognition')
    def test_product_and_product_name_snapshot_cannot_be_rewritten(self):
        order = self.order()
        order.product = self.other
        with self.assertRaises(ValidationError):
            order.save()
        order.refresh_from_db()
        order.product_name = 'Different contract product'
        with self.assertRaises(ValidationError):
            order.save()
        self.plan.product = self.other
        with self.assertRaises(ValidationError):
            self.plan.save()
    def test_publishing_metadata_does_not_sell_an_unimplemented_product(self):
        self.other.is_published = True
        self.other.save()
        plan = Plan.objects.create(product=self.other, slug='test-sku-plan', name='Test-only plan',
            price_irr=1000, duration_days=365, student_limit=100, device_limit=1,
            is_active=True, is_public=True)
        self.assertFalse(self.other.is_available)
        self.assertEqual(supported_product_slugs(), (FACE_PRODUCT_SLUG,))
        with self.assertRaises(CommerceError):
            create_order(self.school, plan.pk, uuid.uuid4(), product_slug=self.other.pk)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)
    def test_product_scoped_endpoint_rejects_cross_product_plan_and_key_replay(self):
        order = self.order()
        with self.assertRaises(CommerceError):
            create_order(self.school, self.plan.pk, order.idempotency_key, product_slug=self.other.pk)
        with self.assertRaises(CommerceError):
            create_order(self.school, self.plan.pk, uuid.uuid4(), product_slug=self.other.pk)
        self.assertEqual(Order.objects.count(), 1)
    def test_unpublished_face_product_cannot_be_bought_or_fulfilled(self):
        order = self.order()
        self.face.is_published = False
        self.face.save()
        with self.assertRaises(CommerceError):
            self.order()
        with self.assertRaises(CommerceError):
            self.pay(order)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)
    def test_payment_updates_face_subscription_and_leaves_other_product_unchanged(self):
        original_end = timezone.now() + timedelta(days=100)
        other_sub = ProductSubscription.objects.create(school=self.school, product=self.other,
            status='active', starts_at=timezone.now()-timedelta(days=1), expires_at=original_end,
            student_limit=17, device_limit=3)
        order = self.order()
        self.pay(order)
        self.school.refresh_from_db()
        face_sub = ProductSubscription.objects.get(school=self.school, product=self.face)
        self.assertEqual(face_sub.expires_at, self.school.expires_at)
        self.assertEqual(face_sub.starts_at, self.school.starts_at)
        self.assertEqual(face_sub.student_limit, 900)
        self.assertEqual(face_sub.device_limit, 1)
        self.assertEqual(face_sub.source_order_id, order.pk)
        other_sub.refresh_from_db()
        self.assertEqual(other_sub.expires_at, original_end)
        self.assertEqual(other_sub.student_limit, 17)
        self.assertEqual(other_sub.device_limit, 3)
        self.assertTrue(self.school.entitled)
    def test_corrupt_order_plan_product_binding_cannot_grant_a_face_license(self):
        order = self.order()
        # ORM bulk updates are outside model.save validation; fulfillment must
        # independently reject mismatched data instead of granting another SKU.
        Plan.objects.filter(pk=self.plan.pk).update(product=self.other)
        with self.assertRaises(CommerceError):
            self.pay(order)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)
    def test_manual_sync_preserves_face_authority_and_other_product_ledger(self):
        first = self.order()
        self.pay(first)
        self.school.status = 'banned'
        self.school.student_limit = 700
        self.school.save(update_fields=['status', 'student_limit'])
        sync_face_subscription(self.school)
        sub = ProductSubscription.objects.get(school=self.school, product=self.face)
        self.assertEqual(sub.status, 'banned')
        self.assertEqual(sub.student_limit, 700)
        self.assertEqual(sub.source_order_id, first.pk)
        self.assertFalse(sub.entitled)
    def test_sync_refuses_another_school_or_product_order(self):
        order = self.order()
        another = School.objects.create(user=get_user_model().objects.create_user('another-product-school@example.com'), name='Another school')
        with self.assertRaises(CommerceError):
            sync_face_subscription(another, source_order=order)
        self.assertFalse(ProductSubscription.objects.filter(school=another).exists())

    def test_plan_slugs_are_unique_within_product_not_across_company(self):
        second = Plan.objects.create(product=self.other, slug=self.plan.slug, name='Unpublished test draft')
        self.assertEqual(second.slug, self.plan.slug)
        self.assertNotEqual(second.product_id, self.plan.product_id)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            # Use an inner savepoint so the surrounding test transaction survives.
            with transaction.atomic():
                Plan.objects.create(product=self.face, slug=self.plan.slug, name='Duplicate same product')

    def test_subscription_uniqueness_is_school_and_product_scoped(self):
        sync_face_subscription(self.school)
        ProductSubscription.objects.create(school=self.school, product=self.other)
        self.assertEqual(ProductSubscription.objects.filter(school=self.school).count(), 2)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductSubscription.objects.create(school=self.school, product=self.face)

    def test_explicit_test_handler_can_fulfill_its_own_product_without_face_changes(self):
        """The registry is extensible; this fake SKU/handler exists only in this test."""
        from unittest.mock import patch
        from .commerce import Entitlement, PRODUCT_FULFILLMENT
        face_order = self.order()
        self.pay(face_order)
        self.school.refresh_from_db()
        before = (self.school.status, self.school.starts_at, self.school.expires_at,
                  self.school.student_limit, self.school.device_limit)
        self.other.is_published = True
        self.other.save()
        plan = Plan.objects.create(product=self.other, slug='test-registered', name='Test-only contract',
            price_irr=1000, duration_days=30, student_limit=10, device_limit=2,
            is_active=True, is_public=True)
        class TestOnlyHandler:
            def current_entitlement(self, school, product):
                sub = ProductSubscription.objects.filter(school=school, product=product).first()
                return Entitlement(sub.starts_at, sub.expires_at, sub.student_limit, sub.device_limit, sub.entitled) if sub else Entitlement(None, None, 0, 1, False)
            def validate_capacity(self, school, contract):
                pass  # This isolated fake has no device/runtime policy.
            def apply(self, school, order, starts_at, expires_at):
                ProductSubscription.objects.update_or_create(school=school, product_id=order.product_id,
                    defaults={'starts_at': starts_at, 'expires_at': expires_at, 'student_limit': order.student_limit,
                              'device_limit': order.device_limit, 'status': 'active', 'source_order': order})
        with patch.dict(PRODUCT_FULFILLMENT, {self.other.pk: TestOnlyHandler()}):
            order = create_order(self.school, plan.pk, uuid.uuid4(), product_slug=self.other.pk)
            verify_manual_payment(order.pk, self.owner, 'BANK-EXTENSION-999', order.amount_irr)
        self.school.refresh_from_db()
        self.assertEqual(before, (self.school.status, self.school.starts_at, self.school.expires_at,
                                  self.school.student_limit, self.school.device_limit))
        sub = ProductSubscription.objects.get(school=self.school, product=self.other)
        self.assertEqual(sub.student_limit, 10)
        self.assertEqual(sub.device_limit, 2)
        self.assertEqual(sub.source_order_id, order.pk)
        self.assertFalse(self.other.is_available)  # No handler persists after the test patch.
