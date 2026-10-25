"""Server-rendered Persian product pages; public demo contains synthetic data only."""
import uuid
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import escape

PUBLIC_PAGES = {
    'home': ('حضور روشن، مدیریت آسوده', 'FaceRecognition؛ حضور و غیاب تصویری برای مدارس، با پردازش محلی، مجوز آفلاین و مدیریت امن اشتراک.'),
    'features': ('قابلیت‌های FaceRecognition', 'از ثبت چهره و تردد تا گزارش‌های محلی و پشتیبان رمزگذاری‌شده؛ قابلیت‌های سامانهٔ مدارس NxFi.'),
    'how-it-works': ('از نصب تا یک روز کاری', 'مراحل بررسی سخت‌افزار، فعال‌سازی اشتراک، ثبت دانش‌آموزان و شروع حضور و غیاب تصویری.'),
    'demo': ('دموی تعاملی با دادهٔ ساختگی', 'بدون دوربین و بدون دادهٔ واقعی دانش‌آموزان، نمونهٔ نمایشی گزارش حضور و غیاب را بررسی کنید.'),
    'pricing': ('طرح متناسب با مدرسهٔ شما', 'طرح‌های قابل تنظیم بر اساس ظرفیت دانش‌آموز، تعداد نصب و مدت اشتراک؛ پرداخت دستی فقط پس از تأیید فعال می‌شود.'),
    'compare': ('انتخاب آگاهانه برای مدرسه', 'مقایسهٔ گردش‌کار دستی با FaceRecognition و توضیح روشن پیش‌نیازها و محدودیت‌ها.'),
    'downloads': ('دریافت نرم‌افزار', 'نسخه‌های منتشرشدهٔ FaceRecognition، یادداشت انتشار و SHA-256 برای بررسی صحت فایل.'),
    'requirements': ('پیش‌نیازهای نصب', 'سخت‌افزار، ویندوز، دوربین، شبکه و الزامات حفاظت از دادهٔ مدارس برای نصب FaceRecognition.'),
    'faq': ('پرسش‌های متداول', 'پاسخ دربارهٔ کارکرد آفلاین، اشتراک، تمدید، اطلاعات چهره، ظرفیت و بازیابی اطلاعات.'),
    'support': ('مرکز راهنما', 'راهنمای شروع، فعال‌سازی آفلاین‌محور، بکاپ رمزگذاری‌شده و ارتباط با پشتیبانی.'),
    'privacy': ('حریم خصوصی و داده‌های مدرسه', 'تفکیک اطلاعات حساب تجاری از سوابق دانش‌آموزان، رضایت برای بکاپ و مسئولیت نگهداری کلید بازیابی.'),
    'terms': ('شرایط استفاده', 'شرایط اشتراک، پرداخت دستی تأییدشده، مجوز آفلاین، مسئولیت استقرار و حدود پشتیبانی FaceRecognition.'),
}


def page(request, page='home'):
    if page not in PUBLIC_PAGES:
        raise Http404
    title, description = PUBLIC_PAGES[page]
    context = {'page_title': title, 'meta_description': description, 'indexable': True,
               'canonical_url': settings.PUBLIC_URL + reverse(page), 'current_page': page}
    if page == 'pricing':
        from .models import Plan
        context['plans'] = list(Plan.objects.filter(is_active=True, is_public=True).order_by('sort_order', 'price_irr', 'pk'))
        for plan in context['plans']:
            plan.checkout_key = uuid.uuid4()
    if page == 'downloads':
        from .models import Release
        context['releases'] = Release.objects.filter(is_published=True).order_by('-created_at')
    return render(request, 'marketing/' + page.replace('-', '_') + '.html', context)


def robots(request):
    return HttpResponse('User-agent: *\nAllow: /\nDisallow: /account/\nDisallow: /owner/\nDisallow: /admin/\nDisallow: /api/\nSitemap: ' + settings.PUBLIC_URL + '/sitemap.xml\n', content_type='text/plain')


def sitemap(request):
    locations = [settings.PUBLIC_URL + reverse(name) for name in PUBLIC_PAGES]
    locations.append(settings.PUBLIC_URL + reverse('contact'))
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    body += ''.join('<url><loc>' + escape(location) + '</loc></url>' for location in locations)
    return HttpResponse(body + '</urlset>', content_type='application/xml')
