"""Owner-created school accounts. Separate from customer self-registration/payments."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, password_validation
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .commerce import sync_face_subscription
from .models import AuditEvent, School
from .owner_auth import locked_company_owner, owner_required


class SchoolProvisionForm(forms.Form):
    name = forms.CharField(label=_("School name"), max_length=160)
    email = forms.EmailField(label=_("School login email"), max_length=150)
    temporary_password = forms.CharField(
        label=_("Temporary school password"), strip=False, max_length=1024,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=_("Share privately with the school and ask them to change it. Never send biometric data by email."),
    )
    student_limit = forms.IntegerField(label=_("Student capacity"), min_value=1, max_value=1000000, initial=900)
    device_limit = forms.IntegerField(label=_("Device allowance"), min_value=1, max_value=100, initial=1)
    subscription_days = forms.IntegerField(label=_("Initial subscription (days)"), min_value=1, max_value=3650, initial=30)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().casefold()
        if get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError(_("An account already uses this email."))
        return email

    def clean(self):
        values = super().clean()
        if "temporary_password" in values and "email" in values:
            user = get_user_model()(username=values["email"], email=values["email"])
            try:
                password_validation.validate_password(values["temporary_password"], user)
            except forms.ValidationError as exc:
                self.add_error("temporary_password", exc)
            except Exception as exc:
                from django.core.exceptions import ValidationError
                if isinstance(exc, ValidationError):
                    self.add_error("temporary_password", exc)
                else:
                    raise
        return values


@owner_required(recent=True)
def create_school(request):
    """Provision a real school account via explicitly authenticated owner action."""
    form = SchoolProvisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            with transaction.atomic():
                locked_company_owner(request.user)
                user = get_user_model().objects.create_user(
                    username=values["email"], email=values["email"],
                    password=values["temporary_password"], is_active=True,
                )
                now = timezone.now()
                school = School.objects.create(
                    user=user, name=values["name"], status=School.Status.ACTIVE,
                    starts_at=now, expires_at=now + timedelta(days=values["subscription_days"]),
                    student_limit=values["student_limit"], device_limit=values["device_limit"],
                )
                sync_face_subscription(school)
                AuditEvent.objects.create(
                    actor=request.user, school=school, action="school.owner_provisioned",
                    object_id=str(school.pk),
                )
        except IntegrityError:
            form.add_error("email", _("An account already uses this email."))
        else:
            messages.success(request, _("School created. Give the school its credentials through a secure channel. No payment has been recorded."))
            return redirect("owner-school", school_id=school.pk)
    return render(request, "owner/create_school.html", {"form": form})
