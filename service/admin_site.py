"""The legacy Django administration is also gated by explicit owner + step-up."""
from django.utils.translation import gettext_lazy as _
from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from urllib.parse import urlencode

from .owner_auth import has_recent_owner_auth, is_company_owner, mark_owner_reauthenticated


class OwnerAdminAuthenticationForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not is_company_owner(user):
            raise ValidationError(_('Only the authorized company owner may sign in to administration.'), code='invalid_login')


class OwnerAdminSite(AdminSite):
    login_form = OwnerAdminAuthenticationForm
    site_header = _('NxFi — administration')
    site_title = _('NxFi administration')
    index_title = _('Technical administration')

    def has_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff and has_recent_owner_auth(request))

    def login(self, request, extra_context=None):
        if request.method == 'GET' and is_company_owner(request.user) and not has_recent_owner_auth(request):
            target = request.GET.get('next', '/admin/')
            return redirect('/owner/reauth/?' + urlencode({'next': target}))
        response = super().login(request, extra_context)
        # A successful admin password login itself provides the required step-up.
        if request.method == 'POST' and response.status_code == 302 and is_company_owner(request.user):
            mark_owner_reauthenticated(request)
        return response
