"""Company-owner authorization and short, password-bound step-up sessions."""
from django.utils.translation import gettext_lazy as _
from functools import wraps
import logging
import math
import time

from django import forms
from django.conf import settings
from django.contrib.auth import update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from .models import AuditEvent, School
from .owner_models import CompanyOwner
from .security import throttled, too_many

logger = logging.getLogger(__name__)
STEP_UP_SESSION_KEY = 'company_owner_step_up'


def is_company_owner(user):
    """Query explicit authority on every check, so replacement revokes old sessions."""
    return bool(user.is_authenticated and user.is_active and
                CompanyOwner.objects.filter(pk=1, user_id=user.pk, user__is_active=True).exists() and
                not School.objects.filter(user_id=user.pk).exists())


def locked_company_owner(actor):
    """Recheck current role and credentials after mutation-row acquisition.

    The authority and actor rows remain locked until the surrounding mutation
    commits. Owner replacement uses the same authority→actor lock order. Call
    after any School/Order row locks, consistently across commercial mutations.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Fresh owner authority requires an atomic transaction')
    authority = CompanyOwner.objects.select_for_update().filter(pk=1).first()
    user = get_user_model().objects.select_for_update().filter(pk=getattr(actor, 'pk', None)).first()
    if (authority is None or user is None or authority.user_id != user.pk or not user.is_active
            or School.objects.filter(user=user).exists()
            or not constant_time_compare(user.get_session_auth_hash(), actor.get_session_auth_hash())):
        raise PermissionDenied(_('Only the designated owner may perform this action.'))
    return authority


def has_recent_owner_auth(request):
    if not is_company_owner(request.user):
        return False
    state = request.session.get(STEP_UP_SESSION_KEY)
    if not isinstance(state, dict):
        return False
    issued = state.get('issued')
    if type(issued) not in (int, float) or not math.isfinite(issued):
        return False
    timeout = max(60, min(900, int(getattr(settings, 'OWNER_REAUTH_SECONDS', 300))))
    if not 0 <= time.time() - issued <= timeout:
        return False
    owner = CompanyOwner.objects.get(pk=1)
    return (state.get('user') == str(request.user.pk) and state.get('revision') == owner.revision
            and isinstance(state.get('auth'), str)
            and constant_time_compare(state['auth'], request.user.get_session_auth_hash()))


def mark_owner_reauthenticated(request):
    if not is_company_owner(request.user):
        raise PermissionError('Explicit company owner required')
    owner = CompanyOwner.objects.get(pk=1)
    request.session[STEP_UP_SESSION_KEY] = {'issued': time.time(), 'user': str(request.user.pk),
        'revision': owner.revision, 'auth': request.user.get_session_auth_hash()}


def owner_required(view=None, *, recent=False):
    """Use recent=True on every sensitive owner mutation; never infer from Django flags."""
    def decorate(function):
        @wraps(function)
        @login_required
        def wrapped(request, *args, **kwargs):
            if not is_company_owner(request.user):
                return render(request, 'error.html', {'code': 403, 'message': _('This section is restricted to the authorized company owner.')}, status=403)
            if recent and not has_recent_owner_auth(request):
                from urllib.parse import urlencode
                return redirect('/owner/reauth/?' + urlencode({'next': request.get_full_path()}))
            return function(request, *args, **kwargs)
        return wrapped
    return decorate(view) if view is not None else decorate


class OwnerReauthenticationForm(forms.Form):
    password = forms.CharField(label=_('Current owner password'), strip=False, max_length=1024,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'autofocus': True}))


@sensitive_post_parameters('password')
@sensitive_variables()
@never_cache
@require_http_methods(['GET', 'POST'])
@owner_required
def owner_reauthenticate(request):
    next_url = request.POST.get('next') or request.GET.get('next') or '/owner/'
    if (not next_url.startswith('/') or next_url.startswith('//') or
            not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=True)):
        next_url = '/owner/'
    form = OwnerReauthenticationForm(request.POST or None)
    if request.method == 'POST':
        try:
            limited = throttled(request, 'owner-step-up', str(request.user.pk), limit=5, seconds=300)
        except Exception:
            logger.error('Owner step-up protection backend unavailable')
            return HttpResponse(_('The security check is temporarily unavailable. Please try again.'), status=503)
        if limited:
            return too_many()
        if form.is_valid():
            if request.user.check_password(form.cleaned_data['password']):
                # Hash upgrades during check_password must not log out this session.
                update_session_auth_hash(request, request.user)
                mark_owner_reauthenticated(request)
                AuditEvent.objects.create(actor=request.user, action='owner.reauthenticated')
                return redirect(next_url)
            form.add_error('password', _('The account password is incorrect.'))
            AuditEvent.objects.create(actor=request.user, action='owner.reauthentication_failed')
    return render(request, 'owner/reauth.html', {'form': form, 'next': next_url})
