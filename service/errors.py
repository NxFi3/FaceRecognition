from django.shortcuts import render


def bad_request(request, exception=None):
    return render(request, 'error.html', {'code': 400, 'message': 'درخواست معتبر نیست؛ صفحه را دوباره باز کنید.'}, status=400)


def forbidden(request, exception=None):
    return render(request, 'error.html', {'code': 403, 'message': 'اجازهٔ این عملیات را ندارید یا بررسی امنیتی فرم لازم است.'}, status=403)


def not_found(request, exception=None):
    return render(request, 'error.html', {'code': 404, 'message': 'صفحه یا رکورد در دسترس این حساب نیست.'}, status=404)


def server_error(request):
    return render(request, 'error.html', {'code': 500, 'message': 'سرویس موقتاً با مشکل روبه‌رو شد؛ دوباره تلاش کنید.'}, status=500)
