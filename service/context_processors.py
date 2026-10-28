"""Company-wide chrome with an explicit first-product boundary."""
from django.conf import settings


def company_navigation(request):
    return {'product_scope': request.path.startswith('/products/facerecognition/'),
            'company_name': 'NxFi',
            'owner_only_web': getattr(settings, 'OWNER_ONLY_WEB', False)}
