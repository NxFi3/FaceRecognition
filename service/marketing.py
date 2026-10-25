"""Company marketing shell and the first product’s complete public experience."""
import uuid
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import escape

PUBLIC_PAGES = {
    'home': ('NxFi Technologies؛ نرم‌افزار و هوش مصنوعی کاربردی', 'آشنایی با NxFi Technologies، شرکت نرم‌افزار و فناوری هوش مصنوعی، و FaceRecognition به‌عنوان نخستین محصول تجاری آن.'),
    'products': ('محصولات NxFi Technologies', 'محصولات معرفی‌شدهٔ NxFi Technologies؛ FaceRecognition نخستین و در حال حاضر تنها محصول تجاری شرکت است.'),
    'product-facerecognition': ('FaceRecognition؛ حضور روشن، مدیریت آسوده', 'محصول حضور و غیاب تصویری NxFi Technologies برای مدارس؛ پردازش محلی، مجوز آفلاین، گزارش و پشتیبانی.'),
    'features': ('قابلیت‌های FaceRecognition', 'از ثبت چهره و تردد تا گزارش‌های محلی و پشتیبان رمزگذاری‌شده؛ قابلیت‌های سامانهٔ مدارس NxFi.'),
    'how-it-works': ('از نصب تا یک روز کاری', 'مراحل بررسی سخت‌افزار، فعال‌سازی اشتراک، ثبت دانش‌آموزان و شروع حضور و غیاب تصویری.'),
    'demo': ('دموی تعاملی با دادهٔ ساختگی', 'بدون دوربین و بدون دادهٔ واقعی دانش‌آموزان، نمونهٔ نمایشی گزارش حضور و غیاب را بررسی کنید.'),
    'pricing': ('طرح متناسب با مدرسهٔ شما', 'طرح‌های قابل تنظیم بر اساس ظرفیت دانش‌آموز، تعداد نصب و مدت اشتراک؛ پرداخت دستی فقط پس از تأیید فعال می‌شود.'),
    'compare': ('انتخاب آگاهانه برای مدرسه', 'مقایسهٔ گردش‌کار دستی با FaceRecognition و توضیح روشن پیش‌نیازها و محدودیت‌ها.'),
    'downloads': ('دریافت نرم‌افزار', 'نسخه‌های منتشرشدهٔ FaceRecognition، یادداشت انتشار و SHA-256 برای بررسی صحت فایل.'),
    'requirements': ('پیش‌نیازهای نصب', 'سخت‌افزار، ویندوز، دوربین، شبکه و الزامات حفاظت از دادهٔ مدارس برای نصب FaceRecognition.'),
    'faq': ('پرسش‌های متداول', 'پاسخ دربارهٔ کارکرد آفلاین، اشتراک، تمدید، اطلاعات چهره، ظرفیت و بازیابی اطلاعات.'),
    'support': ('مرکز راهنمای NxFi', 'پشتیبانی حساب NxFi و راهنمای محصول فعلی FaceRecognition؛ نصب، مجوز آفلاین و بازیابی.'),
    'privacy': ('حریم خصوصی در NxFi Technologies', 'تفکیک اطلاعات حساب تجاری از سوابق دانش‌آموزان، رضایت برای بکاپ و مسئولیت نگهداری کلید بازیابی.'),
    'terms': ('شرایط استفاده از NxFi و محصولات', 'شرایط حساب مشتری NxFi، سفارش و پرداخت و شرایط فنی محصول فعلی FaceRecognition.'),
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
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    body += ''.join('<url><loc>' + escape(location) + '</loc></url>' for location in locations)
    return HttpResponse(body + '</urlset>', content_type='application/xml')
