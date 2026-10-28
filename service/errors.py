from django.utils.translation import gettext_lazy as _
from django.shortcuts import render


def bad_request(request, exception=None):
    return render(request, 'error.html', {'code': 400, 'message': _('Invalid request. Reload the page.')}, status=400)


def forbidden(request, exception=None):
    return render(request, 'error.html', {'code': 403, 'message': _('You cannot perform this action, or the form requires a security check.')}, status=403)


def not_found(request, exception=None):
    return render(request, 'error.html', {'code': 404, 'message': _('This page or record is unavailable to your account.')}, status=404)


def server_error(request):
    return render(request, 'error.html', {'code': 500, 'message': _('The service encountered a temporary problem. Please try again.')}, status=500)
