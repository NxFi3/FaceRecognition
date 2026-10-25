"""Transactional commerce boundary. Browser claims never grant an entitlement.

Only verified manual payments are implemented. Future gateway implementations
must authenticate server-to-server confirmation and return VerifiedEvidence;
a redirect, query string or customer-provided receipt is never that evidence.
"""
from dataclasses import dataclass
from datetime import timedelta
import re
import unicodedata
import uuid
from typing import Protocol

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone

from .models import AuditEvent, School, Device
from .commerce_models import Order, Plan, VerifiedPayment


class CommerceError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedEvidence:
    provider: str
    reference: str
    amount_irr: int


class PaymentProvider(Protocol):
    """Future server-side provider contract, deliberately unavailable at checkout."""
    def verify(self, *, order_id: uuid.UUID, server_reference: str) -> VerifiedEvidence: ...


def _owner(actor):
    from .owner_auth import is_company_owner
    if not is_company_owner(actor):
        raise PermissionDenied('این عملیات فقط برای مالک تعیین‌شده مجاز است.')


def normalize_reference(value):
    if not isinstance(value, str):
        raise CommerceError('شناسهٔ پرداخت معتبر نیست.')
    value = unicodedata.normalize('NFKC', value).strip().upper()
    value = ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in value)
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9._/\-]{5,79}', value):
        raise CommerceError('شناسهٔ پرداخت باید ۶ تا ۸۰ حرف لاتین یا رقم باشد؛ پیشوند بانک را نیز وارد کنید.')
    return value


def _notify(order, subject, body, suffix):
    # Durable outbox in the SAME transaction; SMTP is never inside payment locks.
    from .models import EmailOutbox
    EmailOutbox.objects.get_or_create(dedupe_key=f'order:{order.pk}:{suffix}', defaults={
        'recipient': order.billing_email, 'subject': subject, 'body': body, 'available_at': timezone.now()})


def create_order(school, plan_id, idempotency_key):
    try:
        key = uuid.UUID(str(idempotency_key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CommerceError('شناسهٔ درخواست معتبر نیست؛ صفحهٔ خرید را دوباره باز کنید.') from exc
    with transaction.atomic():
        school = School.objects.select_for_update().select_related('user').get(pk=school.pk)
        if not school.user.is_active or school.status == School.Status.BANNED:
            raise CommerceError('حساب برای سفارش مجاز نیست؛ با پشتیبانی تماس بگیرید.')
        prior = Order.objects.filter(school=school, idempotency_key=key).first()
        if prior:
            if str(prior.plan_id) != str(plan_id):
                raise CommerceError('شناسهٔ درخواست قبلاً برای طرح دیگری استفاده شده است.')
            return prior
        try:
            plan = Plan.objects.select_for_update().get(pk=plan_id, is_active=True, is_public=True)
        except (Plan.DoesNotExist, ValueError, TypeError) as exc:
            raise CommerceError('طرح انتخاب‌شده در حال حاضر قابل سفارش نیست.') from exc
        try:
            plan.full_clean()
        except ValidationError as exc:
            raise CommerceError('تنظیمات این طرح کامل نیست؛ با پشتیبانی تماس بگیرید.') from exc
        now = timezone.now()
        expired = Order.objects.filter(school=school, plan=plan, status__in=[Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_SUBMITTED], expires_at__lte=now)
        for item in expired:
            item.status = Order.Status.EXPIRED
            item.save(update_fields=['status'])
            AuditEvent.objects.create(actor=school.user, school=school, action='order.expired', object_id=str(item.pk))
        pending = Order.objects.filter(school=school, plan=plan, status__in=[Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_SUBMITTED]).first()
        if pending:
            return pending
        if plan.is_trial and Order.objects.filter(school=school, is_trial=True, status=Order.Status.FULFILLED).exists():
            raise CommerceError('دورهٔ آزمایشی این مدرسه قبلاً استفاده شده است.')
        if Device.objects.filter(school=school, revoked=False, token_digest__isnull=False).count() > plan.device_limit:
            raise CommerceError('ابتدا نصب‌های اضافه را از صفحهٔ دستگاه‌ها لغو کنید یا با پشتیبانی تماس بگیرید.')
        if school.entitled and (plan.student_limit < school.student_limit or plan.device_limit < school.device_limit):
            raise CommerceError('کاهش ظرفیت در میانهٔ دوره نیازمند بررسی پشتیبانی است.')
        order = Order.objects.create(school=school, plan=plan, idempotency_key=key,
            purpose=Order.Purpose.TRIAL if plan.is_trial else (Order.Purpose.RENEWAL if school.expires_at else Order.Purpose.PURCHASE),
            plan_name=plan.name, school_name=school.name, billing_email=school.user.email or school.user.username,
            amount_irr=plan.price_irr, duration_days=plan.duration_days,
            student_limit=plan.student_limit, device_limit=plan.device_limit,
            is_trial=plan.is_trial, expires_at=now+timedelta(days=7))
        AuditEvent.objects.create(actor=school.user, school=school, action='order.created', object_id=str(order.pk))
        _notify(order, 'سفارش FaceRecognition ثبت شد', f'سفارش {order.invoice_number} ثبت شد. تا تأیید مستقل پرداخت یا تأیید آزمایش، اشتراک فعال نمی‌شود. مبلغ: {order.amount_irr} ریال. اعتبار سفارش: {order.expires_at.isoformat()}', 'created')
        return order


def submit_manual_payment(school, order_id, reference):
    reference = normalize_reference(reference)
    with transaction.atomic():
        # Keep the same School -> Order ordering as creation and fulfillment;
        # the audit FK must never invert a simultaneous verification lock.
        school = School.objects.select_for_update().get(pk=school.pk)
        order = Order.objects.select_for_update().filter(pk=order_id, school=school).first()
        if order is None:
            raise PermissionDenied('سفارش متعلق به این حساب نیست.')
        if order.is_trial:
            raise CommerceError('طرح آزمایشی پرداخت ندارد؛ منتظر تأیید پشتیبانی بمانید.')
        if not order.can_cancel or order.expires_at <= timezone.now():
            raise CommerceError('این سفارش برای ثبت پرداخت باز نیست.')
        if order.status == Order.Status.PAYMENT_SUBMITTED:
            if order.receipt_reference != reference:
                raise CommerceError('شناسهٔ دیگری برای این سفارش ثبت شده است؛ اصلاح را از پشتیبانی بخواهید.')
            return order
        order.receipt_reference, order.status = reference, Order.Status.PAYMENT_SUBMITTED
        order.save(update_fields=['receipt_reference', 'status'])
        AuditEvent.objects.create(actor=school.user, school=school, action='payment.claim_submitted', object_id=str(order.pk))
        return order


def cancel_order(school, order_id):
    with transaction.atomic():
        # Keep the same School -> Order ordering as creation and fulfillment;
        # the audit FK must never invert a simultaneous verification lock.
        school = School.objects.select_for_update().get(pk=school.pk)
        order = Order.objects.select_for_update().filter(pk=order_id, school=school).first()
        if order is None:
            raise PermissionDenied('سفارش متعلق به این حساب نیست.')
        if order.status == Order.Status.CANCELLED:
            return order
        if not order.can_cancel:
            raise CommerceError('سفارش فعال‌شده قابل لغو خودکار نیست؛ درخواست پشتیبانی ثبت کنید.')
        order.status = Order.Status.CANCELLED
        order.save(update_fields=['status'])
        AuditEvent.objects.create(actor=school.user, school=school, action='order.cancelled', object_id=str(order.pk))
        return order


def _locked_order(order_id):
    # Global lock ordering: School -> Order, shared by creation and all fulfillment.
    school_id = Order.objects.only('school_id').get(pk=order_id).school_id
    school = School.objects.select_for_update().select_related('user').get(pk=school_id)
    order = Order.objects.select_for_update().get(pk=order_id)
    return school, order


def _validate_fulfillment(school, order):
    if not school.user.is_active or school.status == School.Status.BANNED:
        raise CommerceError('حساب غیرفعال یا مسدود است؛ پرداخت باعث رفع مسدودی نمی‌شود.')
    if order.status not in (Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_SUBMITTED):
        raise CommerceError('سفارش برای فعال‌سازی باز نیست.')
    if order.expires_at <= timezone.now():
        raise CommerceError('اعتبار قیمت سفارش پایان یافته؛ سفارش تازه یا بررسی پشتیبانی لازم است.')
    if Device.objects.filter(school=school, revoked=False, token_digest__isnull=False).count() > order.device_limit:
        raise CommerceError('تعداد نصب‌های مجاز موجود از دستگاه‌های سفارش بیشتر است؛ ابتدا نصب‌های اضافه باید لغو شوند.')
    if school.entitled and (order.student_limit < school.student_limit or order.device_limit < school.device_limit):
        raise CommerceError('فعال‌سازی ظرفیت پایین‌تر در میانهٔ اشتراک نیازمند بررسی پشتیبانی است.')


def _fulfill(school, order, actor):
    now = timezone.now()
    # Existing paid time is preserved. A full new period is appended, not prorated.
    continuing = school.expires_at is not None and school.expires_at > now
    start = school.expires_at if continuing else now
    school.starts_at = school.starts_at if continuing and school.starts_at else now
    school.expires_at = start + timedelta(days=order.duration_days)
    school.student_limit, school.device_limit = order.student_limit, order.device_limit
    school.status = School.Status.ACTIVE
    school.save(update_fields=['starts_at', 'expires_at', 'student_limit', 'device_limit', 'status'])
    order.status, order.fulfilled_at, order.approved_by = Order.Status.FULFILLED, now, actor
    order.subscription_starts_at, order.subscription_expires_at = start, school.expires_at
    order.save(update_fields=['status', 'fulfilled_at', 'approved_by', 'subscription_starts_at', 'subscription_expires_at'])
    AuditEvent.objects.create(actor=actor, school=school, action='order.fulfilled', object_id=str(order.pk))
    _notify(order, 'اشتراک FaceRecognition فعال شد', f'سفارش {order.invoice_number} تأیید شد. اعتبار اشتراک: {school.expires_at.isoformat()}. ظرفیت: {school.student_limit} دانش‌آموز و {school.device_limit} دستگاه. برای دریافت مجوز امضاشده، در برنامه بررسی آنلاین انجام دهید.', 'fulfilled')
    return order


def verify_manual_payment(order_id, actor, reference, amount_irr, verification_note=''):
    """Owner attests independent bank settlement; customer/browser input is insufficient."""
    _owner(actor)
    reference = normalize_reference(reference)
    if type(amount_irr) is not int or not 0 < amount_irr <= 10**12:
        raise CommerceError('مبلغ تأییدشده باید عدد صحیح مثبت به ریال باشد.')
    if not isinstance(verification_note, str) or len(verification_note) > 1000:
        raise CommerceError('یادداشت تأیید بیش از حد طولانی است.')
    with transaction.atomic():
        school, order = _locked_order(order_id)
        payment = VerifiedPayment.objects.filter(order=order).first()
        if payment:
            if payment.provider != 'manual' or payment.reference != reference or payment.amount_irr != amount_irr:
                raise CommerceError('این سفارش قبلاً با اطلاعات پرداخت دیگری تأیید شده است.')
            return order
        if order.is_trial or amount_irr != order.amount_irr:
            raise CommerceError('مبلغ واقعیِ واریز باید دقیقاً با مبلغ ثبت‌شدهٔ سفارش برابر باشد.')
        _validate_fulfillment(school, order)
        if VerifiedPayment.objects.filter(provider='manual', reference=reference).exists():
            raise CommerceError('این شناسهٔ پرداخت قبلاً برای سفارش دیگری استفاده شده است.')
        try:
            with transaction.atomic():
                VerifiedPayment.objects.create(order=order, provider='manual', reference=reference,
                    amount_irr=amount_irr, verified_by=actor, verification_note=verification_note)
        except IntegrityError as exc:
            raise CommerceError('این پرداخت هم‌زمان برای سفارش دیگری تأیید شده است؛ وضعیت را دوباره بررسی کنید.') from exc
        AuditEvent.objects.create(actor=actor, school=school, action='payment.verified', object_id=str(order.pk))
        return _fulfill(school, order, actor)


def approve_trial(order_id, actor):
    _owner(actor)
    with transaction.atomic():
        school, order = _locked_order(order_id)
        if order.status == Order.Status.FULFILLED and order.is_trial:
            return order
        if not order.is_trial or order.amount_irr != 0:
            raise CommerceError('این سفارش، درخواست آزمایشی نیست.')
        _validate_fulfillment(school, order)
        if Order.objects.filter(school=school, is_trial=True, status=Order.Status.FULFILLED).exclude(pk=order.pk).exists():
            raise CommerceError('دورهٔ آزمایشی قبلاً استفاده شده است.')
        AuditEvent.objects.create(actor=actor, school=school, action='trial.approved', object_id=str(order.pk))
        return _fulfill(school, order, actor)



def reject_manual_payment(order_id, actor, reason):
    """Owner rejects an unpaid claim; never refunds/reverses a verified settlement."""
    _owner(actor)
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise CommerceError('دلیل رد باید بین ۱ تا ۱۰۰۰ نویسه و فاقد اطلاعات محرمانهٔ بانکی باشد.')
    reason = reason.strip()
    with transaction.atomic():
        school, order = _locked_order(order_id)
        if order.is_trial:
            raise CommerceError('درخواست آزمایشی پرداختی ندارد؛ رد یا لغو درخواست را از مسیر پشتیبانی پیگیری کنید.')
        if order.status == Order.Status.REJECTED:
            if order.rejection_reason != reason:
                raise CommerceError('این سفارش قبلاً با دلیل دیگری رد شده است؛ تصمیم ثبت‌شده بی‌صدا تغییر نمی‌کند.')
            return order
        if order.status not in (Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_SUBMITTED) or VerifiedPayment.objects.filter(order=order).exists():
            raise CommerceError('سفارش پرداخت‌شده، بسته یا لغوشده قابل رد پرداخت نیست؛ رسیدگی را از پشتیبانی پیگیری کنید.')
        order.status, order.rejection_reason = Order.Status.REJECTED, reason
        order.save(update_fields=['status', 'rejection_reason'])
        AuditEvent.objects.create(actor=actor, school=school, action='order.payment_rejected', object_id=str(order.pk))
        _notify(order, 'نتیجهٔ بررسی پرداخت FaceRecognition', f'پرداخت سفارش {order.invoice_number} تأیید نشد. دلیل: {reason}\nهیچ اشتراک جدیدی فعال نشده است. برای پیگیری با پشتیبانی تماس بگیرید؛ در صورت نیاز سفارش تازه بسازید.', 'rejected')
        return order
