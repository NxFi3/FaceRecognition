import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class School(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "فعال"
        SUSPENDED = "suspended", "تعلیق"
        BANNED = "banned", "مسدود"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField("نام مدرسه", max_length=160)
    status = models.CharField("وضعیت اشتراک", max_length=16, choices=Status.choices, default=Status.SUSPENDED)
    starts_at = models.DateTimeField("شروع اشتراک (UTC)", null=True, blank=True)
    expires_at = models.DateTimeField("پایان اشتراک (UTC)", null=True, blank=True)
    student_limit = models.PositiveIntegerField("ظرفیت رکورد دانش‌آموز", default=0, validators=[MaxValueValidator(1000000)], help_text="رکوردهای فعال برای ظرفیت شمرده می‌شوند؛ آرشیو داده‌ها را حذف نمی‌کند. صفر یعنی بدون دسترسی.")
    device_limit = models.PositiveSmallIntegerField("تعداد دستگاه مجاز", default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    backup_quota_bytes = models.PositiveBigIntegerField(default=1024 ** 3)
    backup_retention = models.PositiveSmallIntegerField("تعداد بکاپ نگهداری‌شده", default=7, validators=[MinValueValidator(1), MaxValueValidator(100)])
    backups_enabled = models.BooleanField("اجازهٔ بکاپ آنلاین", default=True)
    email_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValidationError("پایان اشتراک باید بعد از شروع آن باشد.")
        if self.device_limit < 1 or not 1 <= self.backup_retention <= 100:
            raise ValidationError("تعداد دستگاه حداقل یک و تعداد بکاپ بین ۱ تا ۱۰۰ است.")

    @property
    def entitled(self):
        now = timezone.now()
        return bool(self.user.is_active and self.status == self.Status.ACTIVE and self.starts_at
                    and self.expires_at and self.starts_at <= now < self.expires_at and self.student_limit > 0)

    class Meta:
        verbose_name = "مدرسه"
        verbose_name_plural = "مدارس"

    def __str__(self):
        return self.name


class Device(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    installation_id = models.UUIDField()
    label = models.CharField(max_length=80)
    token_digest = models.CharField(max_length=64, unique=True, null=True, editable=False)
    password_stamp = models.CharField(max_length=64, editable=False)
    credential_expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)
    last_seen = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["school", "installation_id"], name="school_installation_unique")]


class Backup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.PROTECT)
    blob_name = models.CharField(max_length=64, unique=True, editable=False)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    format = models.CharField(max_length=20, default="legacy-sqlite", choices=[("legacy-sqlite", "Legacy SQLite"), ("client-v2", "Client-encrypted complete recovery")])
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    school = models.ForeignKey(School, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
