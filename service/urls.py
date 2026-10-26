from django.utils.translation import gettext_lazy as _
from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from django.http import HttpResponsePermanentRedirect
from django.urls import reverse
from functools import wraps
from . import api, backups, views, marketing, platform, customer, owner_school
from .owner_auth import owner_reauthenticate

urlpatterns = [
    path("language/", customer.language, name="set_language"),
    path("about/", marketing.about, name="about"),
    path("account/profile/company/", customer.profile, name="customer-profile"),
    path("account/products/facerecognition/create/", customer.create_face_workspace, name="create-face-workspace"),
    path("", marketing.page, name="home"),
    path("admin/", admin.site.urls), path("health/", views.health),
    path("products/facerecognition/account/", platform.dashboard, name="account"),
    path("register/", views.register, name="register"), path("verify/<str:token>/", views.verify, name="verify"),
    path("resend-verification/", views.resend, name="resend"),
    path("login/", views.SchoolLoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password-reset/", auth.PasswordResetView.as_view(template_name="registration/password_reset_form.html", email_template_name="registration/password_reset_email.txt"), name="password_reset"),
    path("password-reset/done/", auth.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(template_name="registration/password_reset_confirm.html"), name="password_reset_confirm"),
    path("reset/done/", auth.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"), name="password_reset_complete"),
    path("account/", platform.workspace, name="workspace"),
    path("products/facerecognition/account/backups/<uuid:backup_id>/", views.web_download, name="web-download"),
    path("products/facerecognition/account/backups/<uuid:backup_id>/delete/", views.delete_backup, name="delete-backup"),
    path("products/facerecognition/account/devices/<int:device_id>/revoke/", views.revoke_device, name="revoke-device"),
    path("api/v1/login/", api.login), path("api/v1/lease/", api.lease), path("api/v1/logout/", api.logout),
    path("api/v1/backups/", backups.listing), path("api/v1/backups/upload/", backups.upload),
    path("api/v1/backups/<uuid:backup_id>/", backups.download),
]
admin.site.site_header = _('School subscription management')
admin.site.site_title = _('Subscription management')


# Corporate pages and the complete first-product journey have distinct canonical URLs.
FACE_PREFIX = 'products/facerecognition/'
FACE_PUBLIC = ('features', 'how-it-works', 'demo', 'pricing', 'compare', 'downloads', 'requirements', 'faq')
urlpatterns += [path('products/', marketing.page, {'page': 'products'}, name='products'),
                path(FACE_PREFIX, marketing.page, {'page': 'product-facerecognition'}, name='product-facerecognition')]
urlpatterns += [path(FACE_PREFIX + name + '/', marketing.page, {'page': name}, name=name) for name in FACE_PUBLIC]
urlpatterns += [path(name + '/', marketing.page, {'page': name}, name=name) for name in ('support', 'privacy', 'terms')]


urlpatterns += [
    path('contact/', platform.contact, name='contact'),
    path('robots.txt', marketing.robots, name='robots'), path('sitemap.xml', marketing.sitemap, name='sitemap'),
    path('products/facerecognition/account/profile/', platform.profile, name='profile'),
    path('products/facerecognition/account/security/password/', auth.PasswordChangeView.as_view(template_name='portal/password_change.html', success_url='/products/facerecognition/account/security/password/done/'), name='password_change'),
    path('products/facerecognition/account/security/password/done/', auth.PasswordChangeDoneView.as_view(template_name='portal/password_done.html'), name='password_change_done'),
    path('products/facerecognition/account/orders/', platform.orders, name='orders'),
    path('products/facerecognition/account/orders/new/<int:plan_id>/', platform.order_create, name='create-order'),
    path('products/facerecognition/account/orders/<uuid:order_id>/', platform.checkout, name='checkout'),
    path('products/facerecognition/account/orders/<uuid:order_id>/invoice/', platform.invoice, name='invoice'),
    path('products/facerecognition/account/orders/<uuid:order_id>/cancel/', platform.order_cancel, name='order-cancel'),
    path('products/facerecognition/account/tickets/', platform.tickets, name='tickets'),
    path('products/facerecognition/account/tickets/<uuid:ticket_id>/', platform.ticket, name='ticket'),
    path('products/facerecognition/account/downloads/<uuid:release_id>/', platform.release_download, name='release-download'),
    path('products/facerecognition/account/export/', platform.business_export, name='business-export'),
    path('owner/', platform.owner, name='owner'), path('owner/reauth/', owner_reauthenticate, name='owner-reauth'),
    path('owner/orders/<uuid:order_id>/reject/', platform.owner_reject_payment, name='owner-reject-payment'),
    path('owner/orders/<uuid:order_id>/verify/', platform.owner_payment, name='owner-payment'),
    path('owner/orders/<uuid:order_id>/approve-trial/', platform.owner_trial, name='owner-trial'),
    path('owner/plans/new/', platform.owner_plan, name='owner-plan-new'),
    path('owner/plans/<int:plan_id>/', platform.owner_plan, name='owner-plan'),
    path('owner/releases/new/', platform.owner_release, name='owner-release'),
    path('owner/releases/<uuid:release_id>/withdraw/', platform.owner_release_withdraw, name='owner-release-withdraw'),
    path('owner/tickets/<uuid:ticket_id>/', platform.owner_ticket, name='owner-ticket'),
    path('owner/contacts/<int:contact_id>/handled/', platform.owner_contact, name='owner-contact'),
    path('owner/schools/new/', owner_school.create_school, name='owner-school-new'),
    path('owner/schools/<uuid:school_id>/', platform.owner_school, name='owner-school'),
    path('owner/schools/<uuid:school_id>/devices/<int:device_id>/revoke/', platform.owner_device_revoke, name='owner-device-revoke'),
    path('owner/health/', platform.owner_health, name='owner-health'),
]
handler400 = 'service.errors.bad_request'
handler403 = 'service.errors.forbidden'
handler404 = 'service.errors.not_found'
handler500 = 'service.errors.server_error'


def legacy_product_route(view, target_name, *, mutation=False, stream=False):
    @wraps(view)
    def compatibility(request, *args, **kwargs):
        if request.method in ('GET', 'HEAD') and not mutation and not stream:
            target = reverse(target_name, args=args, kwargs=kwargs)
            query = request.META.get('QUERY_STRING')
            return HttpResponsePermanentRedirect(target + ('?' + query if query else ''))
        return view(request, *args, **kwargs)
    return compatibility


for name in FACE_PUBLIC:
    def redirect_public(request, page_name=name):
        target = reverse(page_name)
        query = request.META.get('QUERY_STRING')
        return HttpResponsePermanentRedirect(target + ('?' + query if query else ''))
    urlpatterns.append(path(name + '/', redirect_public, name='legacy-' + name))

# Desktop website links and already-open customer forms remain compatible.
urlpatterns += [
    path('account/profile/', legacy_product_route(platform.profile, 'profile')),
    path('account/orders/', legacy_product_route(platform.orders, 'orders')),
    path('account/orders/new/<int:plan_id>/', legacy_product_route(platform.order_create, 'create-order', mutation=True)),
    path('account/orders/<uuid:order_id>/', legacy_product_route(platform.checkout, 'checkout')),
    path('account/orders/<uuid:order_id>/invoice/', legacy_product_route(platform.invoice, 'invoice')),
    path('account/orders/<uuid:order_id>/cancel/', legacy_product_route(platform.order_cancel, 'order-cancel', mutation=True)),
    path('account/tickets/', legacy_product_route(platform.tickets, 'tickets')),
    path('account/tickets/<uuid:ticket_id>/', legacy_product_route(platform.ticket, 'ticket')),
    path('account/downloads/<uuid:release_id>/', legacy_product_route(platform.release_download, 'release-download', stream=True)),
    path('account/export/', legacy_product_route(platform.business_export, 'business-export')),
    path('account/backups/<uuid:backup_id>/', legacy_product_route(views.web_download, 'web-download', stream=True)),
    path('account/backups/<uuid:backup_id>/delete/', legacy_product_route(views.delete_backup, 'delete-backup', mutation=True)),
    path('account/devices/<int:device_id>/revoke/', legacy_product_route(views.revoke_device, 'revoke-device', mutation=True)),
    path('account/security/password/', legacy_product_route(auth.PasswordChangeView.as_view(template_name='portal/password_change.html', success_url='/products/facerecognition/account/security/password/done/'), 'password_change')),
    path('account/security/password/done/', legacy_product_route(auth.PasswordChangeDoneView.as_view(template_name='portal/password_done.html'), 'password_change_done')),
]
