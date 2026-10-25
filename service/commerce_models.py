"""Business records only: these models never contain student or biometric data."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Plan(models.Model):
    slug = models.SlugField(unique=True, max_length=60)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=400, blank=True)
    price_irr = models.PositiveBigIntegerField(default=0, validators=[MaxValueValidator(10**12)])
    duration_days = models.PositiveSmallIntegerField(default=365, validators=[MinValueValidator(1), MaxValueValidator(1098)])
    student_limit = models.PositiveIntegerField(default=900, validators=[MinValueValidator(1), MaxValueValidator(1000000)])
    device_limit = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    is_trial = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'price_irr', 'pk']
        constraints = [
            models.CheckConstraint(condition=Q(price_irr__lte=10**12), name='plan_price_bounded'),
            models.CheckConstraint(condition=Q(duration_days__gte=1, duration_days__lte=1098), name='plan_duration_bounded'),
            models.CheckConstraint(condition=Q(student_limit__gte=1, student_limit__lte=1000000), name='plan_students_bounded'),
            models.CheckConstraint(condition=Q(device_limit__gte=1, device_limit__lte=100), name='plan_devices_bounded'),
            models.CheckConstraint(condition=Q(is_trial=False) | Q(price_irr=0, duration_days__lte=30), name='trial_zero_price_short_duration'),
            models.CheckConstraint(condition=Q(is_active=False) | Q(is_trial=True) | Q(price_irr__gt=0), name='active_paid_plan_has_price'),
        ]

    def clean(self):
        if self.is_trial and (self.price_irr != 0 or self.duration_days > 30):
            raise ValidationError('طرح آزمایشی باید رایگان و حداکثر ۳۰ روزه باشد.')
        if self.is_active and not self.is_trial and self.price_irr <= 0:
            raise ValidationError('پیش از فعال‌کردن طرح پولی، قیمت واقعی را وارد کنید.')

    def __str__(self):
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        AWAITING_PAYMENT = 'awaiting_payment', 'در انتظار پرداخت'
        PAYMENT_SUBMITTED = 'payment_submitted', 'در انتظار بررسی پرداخت'
        FULFILLED = 'fulfilled', 'فعال‌شده'
        CANCELLED = 'cancelled', 'لغوشده'
        REJECTED = 'rejected', 'پرداخت ردشده'
        EXPIRED = 'expired', 'مهلت سفارش پایان یافته'
    class Purpose(models.TextChoices):
        PURCHASE = 'purchase', 'خرید'
        RENEWAL = 'renewal', 'تمدید'
        TRIAL = 'trial', 'درخواست آزمایش'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey('service.School', on_delete=models.PROTECT, related_name='orders')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='orders')
    idempotency_key = models.UUIDField(default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AWAITING_PAYMENT)
    # Immutable server-side contract snapshot: editing a plan cannot reprice an invoice.
    plan_name = models.CharField(max_length=100)
    school_name = models.CharField(max_length=160)
    billing_email = models.EmailField()
    amount_irr = models.PositiveBigIntegerField(validators=[MaxValueValidator(10**12)])
    duration_days = models.PositiveSmallIntegerField()
    student_limit = models.PositiveIntegerField()
    device_limit = models.PositiveSmallIntegerField()
    is_trial = models.BooleanField(default=False)
    receipt_reference = models.CharField(max_length=80, blank=True)
    rejection_reason = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    fulfilled_at = models.DateTimeField(null=True, blank=True, editable=False)
    subscription_starts_at = models.DateTimeField(null=True, blank=True, editable=False)
    subscription_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_commercial_orders')

    SNAPSHOT_FIELDS = ('school_id', 'plan_id', 'idempotency_key', 'purpose', 'plan_name', 'school_name', 'billing_email', 'amount_irr', 'duration_days', 'student_limit', 'device_limit', 'is_trial', 'created_at', 'expires_at')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['school', 'idempotency_key'], name='school_order_idempotency_unique'),
            models.UniqueConstraint(fields=['school', 'plan'], condition=Q(status__in=['awaiting_payment', 'payment_submitted']), name='one_pending_school_plan_order'),
            models.CheckConstraint(condition=Q(amount_irr__lte=10**12), name='order_amount_bounded'),
            models.CheckConstraint(condition=Q(duration_days__gte=1, duration_days__lte=1098), name='order_duration_bounded'),
            models.CheckConstraint(condition=Q(student_limit__gte=1, student_limit__lte=1000000), name='order_students_bounded'),
            models.CheckConstraint(condition=Q(device_limit__gte=1, device_limit__lte=100), name='order_devices_bounded'),
            models.CheckConstraint(condition=Q(is_trial=False) | Q(amount_irr=0), name='trial_order_free'),
        ]
        indexes = [models.Index(fields=['school', 'status'], name='order_school_status_idx')]

    @property
    def invoice_number(self):
        # Full UUID avoids short-number collisions; no legal tax-invoice claim.
        return 'NX-' + self.id.hex.upper()

    @property
    def can_cancel(self):
        return self.status in (self.Status.AWAITING_PAYMENT, self.Status.PAYMENT_SUBMITTED)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values(*self.SNAPSHOT_FIELDS, 'rejection_reason').first()
            if previous and any(previous[name] != getattr(self, name) for name in self.SNAPSHOT_FIELDS):
                raise ValidationError('جزئیات مالی سفارش ثبت‌شده قابل تغییر نیست؛ سفارش جدید بسازید.')
            if previous and previous['rejection_reason'] and previous['rejection_reason'] != self.rejection_reason:
                raise ValidationError('دلیل ردِ ثبت‌شده قابل بازنویسی نیست؛ توضیح تکمیلی را در پشتیبانی ثبت کنید.')
        return super().save(*args, **kwargs)


class VerifiedPayment(models.Model):
    """Only independently verified positive manual payments; not customer claims."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name='verified_payment')
    provider = models.CharField(max_length=24, default='manual', editable=False)
    reference = models.CharField(max_length=80)
    amount_irr = models.PositiveBigIntegerField()
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='verified_commercial_payments')
    verified_at = models.DateTimeField(default=timezone.now, editable=False)
    verification_note = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ['-verified_at']
        constraints = [
            models.UniqueConstraint(fields=['provider', 'reference'], name='payment_provider_reference_unique'),
            models.CheckConstraint(condition=Q(amount_irr__gt=0, amount_irr__lte=10**12), name='verified_payment_positive_bounded'),
        ]
