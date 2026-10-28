"""Owner-only website surface for deployments that explicitly opt in.

Never changes the signed desktop license API or school backup API. Ordinary
legacy deployments remain unchanged when the flag/middleware is disabled.
"""
from django.conf import settings
from django.http import HttpResponseNotFound
from django.shortcuts import redirect
from .owner_auth import is_company_owner


class OwnerOnlyWebMiddleware:
    allowed = ("/owner/", "/admin/", "/login/", "/logout/", "/language/",
               "/api/v1/", "/health/", "/static/",
               "/password-reset/", "/reset/")
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        if not getattr(settings, "OWNER_ONLY_WEB", False):
            return self.get_response(request)
        path = request.path_info
        if path == "/":
            return redirect("/owner/" if is_company_owner(request.user) else "/login/")
        if path.startswith(self.allowed):
            return self.get_response(request)
        return HttpResponseNotFound("This service provides administration only.")
