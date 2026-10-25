from cryptography.exceptions import InvalidTag
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.views import LoginView, PasswordResetView
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import connection, transaction, IntegrityError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .backups import download_response
from .models import AuditEvent, Backup, Device, School, CustomerProfile
from django.utils.translation import gettext_lazy as _, get_language, override
from .security import throttled, too_many


class RegistrationForm(UserCreationForm):
    username = forms.EmailField(label=_("Email"), max_length=150)
    school_name = forms.CharField(label=_("School name (optional, for FaceRecognition)"), max_length=160, required=False)
    class Meta:
        model = get_user_model()
        fields = ("username", "school_name", "password1", "password2")
    def clean_username(self):
        value = self.cleaned_data["username"].strip().casefold()
        if get_user_model().objects.filter(username=value).exists():
            raise forms.ValidationError(_("This email cannot be registered. Try signing in or recovering your password."))
        return value


class SchoolAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label=_("NxFi account email"), max_length=150)
    def clean_username(self):
        return self.cleaned_data["username"].strip().casefold()


class SchoolLoginView(LoginView):
    authentication_form = SchoolAuthenticationForm
    template_name = "registration/login.html"
    def form_valid(self, form):
        from .owner_auth import is_company_owner
        if not is_company_owner(form.get_user()) and not (School.objects.filter(user=form.get_user()).exists() or CustomerProfile.objects.filter(user=form.get_user(), email_verified_at__isnull=False).exists()):
            form.add_error(None, _("No verified NxFi account was found."))
            return self.form_invalid(form)
        return super().form_valid(form)


def send_verification(user):
    token = signing.dumps({"user": user.pk, "email": user.email}, salt="verify-school-email")
    url = settings.PUBLIC_URL + reverse("verify", args=[token])
    profile = CustomerProfile.objects.filter(user=user).first()
    with override(profile.preferred_language if profile else "en"):
        send_mail(_("Verify your NxFi account"), str(_("Open this link to verify your account email. Valid for 24 hours.")) + "\n" + url, settings.DEFAULT_FROM_EMAIL, [user.email])


def register(request):
    if not settings.TESTING and not settings.EMAIL_HOST:
        return HttpResponse(_("Registration is unavailable until the operator configures email."), status=503)
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = form.cleaned_data["username"]
                user.is_active = False
                user.save()
                CustomerProfile.objects.create(user=user, preferred_language="fa" if get_language() == "fa" else "en")
                if form.cleaned_data["school_name"]:
                    School.objects.create(user=user, name=form.cleaned_data["school_name"])
        except IntegrityError:
            form.add_error("username", _("This email cannot be registered. Try signing in or recovering your password."))
            return render(request, "accounts/register.html", {"form": form})
        try:
            send_verification(user)
        except OSError:
            messages.error(request, _("Email could not be sent. Use resend verification."))
        else:
            messages.success(request, _("Verification email sent. Verify your email, then choose a product. Subscription activation requires authorized approval."))
        return redirect("login")
    return render(request, "accounts/register.html", {"form": form})


def verify(request, token):
    try:
        data = signing.loads(token, salt="verify-school-email", max_age=86400)
        user = get_user_model().objects.get(pk=data["user"], email=data["email"])
    except (signing.BadSignature, KeyError, get_user_model().DoesNotExist):
        return HttpResponse(_("This link is invalid or expired."), status=400)
    with transaction.atomic():
        school = School.objects.select_for_update().filter(user=user).first()
        customer, _created = CustomerProfile.objects.get_or_create(user=user)
        if school is not None and school.status == School.Status.BANNED:
            return HttpResponse(_("Contact support."), status=403)
        if customer.email_verified_at is None and (school is None or school.email_verified_at is None):
            customer.email_verified_at = timezone.now()
            customer.save(update_fields=["email_verified_at"])
            if school is not None:
                school.email_verified_at = customer.email_verified_at
                school.save(update_fields=["email_verified_at"])
            user.is_active = True
            user.save(update_fields=["is_active"])
        elif not user.is_active:
            return HttpResponse(_("Contact support."), status=403)
    messages.success(request, _("Email verified. You can now sign in."))
    return redirect("login")


def resend(request):
    if request.method == "POST":
        email = str(request.POST.get("email", "")).strip().casefold()[:150]
        user = get_user_model().objects.filter(username=email, is_active=False, school__email_verified_at__isnull=True).exclude(school__status=School.Status.BANNED).first()
        if user:
            try:
                send_verification(user)
            except OSError:
                pass
        messages.success(request, _("If the account needs verification, a link will be sent."))
        return redirect("login")
    return render(request, "accounts/resend.html")


@login_required
def account(request):
    from .owner_auth import is_company_owner
    if is_company_owner(request.user):
        return redirect("owner")
    school = School.objects.filter(user=request.user).first()
    if school is None:
        raise Http404
    return render(request, "accounts/account.html", {"school": school, "backups": Backup.objects.filter(school=school), "devices": Device.objects.filter(school=school)})


@login_required
def web_download(request, backup_id):
    school = School.objects.filter(user=request.user).first()
    backup = Backup.objects.filter(pk=backup_id, school=school).first()
    if backup is None:
        raise Http404
    if throttled(request, "web-backup-download", str(school.pk), limit=20, seconds=3600):
        return too_many()
    try:
        response = download_response(backup)
    except (OSError, ValueError, InvalidTag):
        return HttpResponse("فایل پشتیبان در دسترس نیست؛ با پشتیبانی تماس بگیرید.", status=503)
    AuditEvent.objects.create(actor=request.user, school=school, action="backup.downloaded", object_id=str(backup.pk))
    return response


@login_required
@require_POST
def revoke_device(request, device_id):
    with transaction.atomic():
        # Use the same School -> Device lock order as signed-license issuance.
        school = School.objects.select_for_update().filter(user=request.user).first()
        device = Device.objects.select_for_update().filter(pk=device_id, school=school).first()
        if device is None:
            raise Http404
        device.revoked = True
        # Preserve the digest so the next authenticated sync gets signed denial.
        device.save(update_fields=["revoked"])
        AuditEvent.objects.create(actor=request.user, school=school, action="device.revoked", object_id=str(device.pk))
    return redirect("account")


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        cache.set("health-check", True, 5)
        if cache.get("health-check") is not True:
            raise RuntimeError()
    except Exception:
        return JsonResponse({"ok": False}, status=503)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def delete_backup(request, backup_id):
    from .backups import location
    with transaction.atomic():
        school = School.objects.select_for_update().filter(user=request.user).first()
        backup = Backup.objects.filter(pk=backup_id, school=school).first()
        if backup is None:
            raise Http404
        path = location(backup.school_id, backup.blob_name)
        identifier = str(backup.pk)
        backup.delete()
        transaction.on_commit(lambda: path.unlink(missing_ok=True), robust=True)
        AuditEvent.objects.create(actor=request.user, school=school, action='backup.deleted', object_id=identifier)
    messages.success(request, 'بکاپ انتخاب‌شده حذف شد؛ نسخه‌های جداگانهٔ بازیابی سرور تابع سیاست نگهداری خود هستند.')
    return redirect('account')
