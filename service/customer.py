"""Company-level profile and explicit first-product workspace provisioning."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from .models import CustomerProfile, School, AuditEvent


class ProfileForm(forms.ModelForm):
    class Meta:
        model = CustomerProfile
        fields = ['display_name']
        labels = {'display_name': _('Display name')}


@login_required
def profile(request):
    customer, _created = CustomerProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, instance=customer)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            AuditEvent.objects.create(actor=request.user, action='customer.profile.updated', object_id=str(request.user.pk))
        return redirect('workspace')
    return render(request, 'portal/form.html', {'title': _('NxFi profile'), 'form': form})


class WorkspaceForm(forms.Form):
    school_name = forms.CharField(label=_('School name'), max_length=160)


@login_required
@require_POST
def create_face_workspace(request):
    form = WorkspaceForm(request.POST)
    if not form.is_valid():
        return render(request, 'portal/form.html', {'title': _('Create FaceRecognition workspace'), 'form': form}, status=400)
    # Same User lock serializes concurrent first-workspace requests, without
    # granting any subscription or bypassing existing product entitlement logic.
    with transaction.atomic():
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        from .owner_auth import is_company_owner
        if is_company_owner(user):
            return HttpResponseForbidden(_("The company owner account cannot become a school workspace."))
        customer = CustomerProfile.objects.filter(user=user).first()
        if not user.is_active or not customer or not customer.email_verified_at:
            return HttpResponseForbidden(_('Verify your account email before creating a product workspace.'))
        school, created = School.objects.get_or_create(user=user, defaults={
            'name': form.cleaned_data['school_name'], 'email_verified_at': customer.email_verified_at})
        if created:
            AuditEvent.objects.create(actor=user, school=school, action='customer.workspace.created', object_id=str(school.pk))
    return redirect('account')


def language(request):
    from django.views.i18n import set_language
    response = set_language(request)
    selected = request.POST.get('language') if request.method == 'POST' else None
    if selected in ('en', 'fa') and request.user.is_authenticated:
        customer, _created = CustomerProfile.objects.get_or_create(user=request.user)
        customer.preferred_language = selected
        customer.save(update_fields=['preferred_language'])
    return response
