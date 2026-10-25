"""Company marketing shell and the first product’s complete public experience."""
import uuid
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import gettext_lazy as _

PUBLIC_PAGES = {
    'home': (_('NxFi Technologies — software and applied AI'), _('Explore NxFi Technologies — software and applied AI. Official NxFi Technologies product information and customer services.')),
    'products': (_('Products by NxFi Technologies'), _('Explore Products by NxFi Technologies. Official NxFi Technologies product information and customer services.')),
    'product-facerecognition': (_('FaceRecognition — local school attendance'), _('Explore FaceRecognition — local school attendance. Official NxFi Technologies product information and customer services.')),
    'features': (_('FaceRecognition features'), _('Explore FaceRecognition features. Official NxFi Technologies product information and customer services.')),
    'how-it-works': (_('How FaceRecognition works'), _('Explore How FaceRecognition works. Official NxFi Technologies product information and customer services.')),
    'demo': (_('Interactive synthetic demonstration'), _('Explore Interactive synthetic demonstration. Official NxFi Technologies product information and customer services.')),
    'pricing': (_('FaceRecognition plans and pricing'), _('Explore FaceRecognition plans and pricing. Official NxFi Technologies product information and customer services.')),
    'compare': (_('Compare attendance workflows'), _('Explore Compare attendance workflows. Official NxFi Technologies product information and customer services.')),
    'downloads': (_('Download FaceRecognition'), _('Explore Download FaceRecognition. Official NxFi Technologies product information and customer services.')),
    'requirements': (_('Installation requirements'), _('Explore Installation requirements. Official NxFi Technologies product information and customer services.')),
    'faq': (_('Frequently asked questions'), _('Explore Frequently asked questions. Official NxFi Technologies product information and customer services.')),
    'support': (_('NxFi support center'), _('Explore NxFi support center. Official NxFi Technologies product information and customer services.')),
    'privacy': (_('Privacy policy'), _('Explore Privacy policy. Official NxFi Technologies product information and customer services.')),
    'terms': (_('Terms of service'), _('Explore Terms of service. Official NxFi Technologies product information and customer services.')),
}


# A product is public only after BOTH runtime fulfillment and a real landing exist.
# Adding a launched product extends this registry instead of replacing the company site.
PRODUCT_LANDINGS = {'facerecognition': 'product-facerecognition'}


PRODUCT_PAGES = frozenset(('product-facerecognition', 'features', 'how-it-works', 'demo', 'pricing', 'compare', 'downloads', 'requirements', 'faq'))


def page(request, page='home'):
    if page not in PUBLIC_PAGES:
        raise Http404
    title, description = PUBLIC_PAGES[page]
    from .models import Product
    from .commerce_models import FACE_PRODUCT_SLUG
    from .commerce import supported_product_slugs
    launched = set(PRODUCT_LANDINGS).intersection(supported_product_slugs())
    products = list(Product.objects.filter(is_published=True, slug__in=launched).order_by('sort_order', 'name'))
    for entry in products:
        entry.landing_url = reverse(PRODUCT_LANDINGS[entry.pk])
    product = next((entry for entry in products if entry.pk == FACE_PRODUCT_SLUG), None) if page in PRODUCT_PAGES else None
    if page in PRODUCT_PAGES and product is None:
        raise Http404
    context = {'page_title': title, 'meta_description': description, 'indexable': True,
               'canonical_url': settings.PUBLIC_URL + reverse(page), 'current_page': page,
               'products': products, 'product': product, 'product_scope': page in PRODUCT_PAGES}
    if page == 'pricing':
        from .models import Plan
        context['plans'] = list(Plan.objects.filter(product=product, is_active=True, is_public=True).order_by('sort_order', 'price_irr', 'pk'))
        for plan in context['plans']:
            plan.checkout_key = uuid.uuid4()
    if page == 'downloads':
        from .models import Release
        context['releases'] = Release.objects.filter(product=product, is_published=True).order_by('-created_at')
    template = 'facerecognition' if page == 'product-facerecognition' else page.replace('-', '_')
    return render(request, 'marketing/' + template + '.html', context)


def robots(request):
    return HttpResponse('User-agent: *\nAllow: /\nDisallow: /account/\nDisallow: /products/facerecognition/account/\nDisallow: /owner/\nDisallow: /admin/\nDisallow: /api/\nSitemap: ' + settings.PUBLIC_URL + '/sitemap.xml\n', content_type='text/plain')


def sitemap(request):
    from .models import Product
    from .commerce_models import FACE_PRODUCT_SLUG
    face_published = Product.objects.filter(pk=FACE_PRODUCT_SLUG, is_published=True).exists()
    locations = [settings.PUBLIC_URL + reverse(name) for name in PUBLIC_PAGES
                 if face_published or name not in PRODUCT_PAGES]
    locations.append(settings.PUBLIC_URL + reverse('contact'))
    locations.append(settings.PUBLIC_URL + reverse('about'))
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    body += ''.join('<url><loc>' + escape(location) + '</loc></url>' for location in locations)
    return HttpResponse(body + '</urlset>', content_type='application/xml')


def about(request):
    return render(request, 'marketing/about.html', {'indexable': True, 'page_title': _('About NxFi'), 'canonical_url': settings.PUBLIC_URL + reverse('about')})
