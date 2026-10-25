from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from django.views.generic import RedirectView
from . import api, backups, views, marketing, platform
from .owner_auth import owner_reauthenticate

urlpatterns = [
    path("", marketing.page, name="home"),
    path("admin/", admin.site.urls), path("health/", views.health),
    path("register/", views.register, name="register"), path("verify/<str:token>/", views.verify, name="verify"),
    path("resend-verification/", views.resend, name="resend"),
    path("login/", views.SchoolLoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password-reset/", auth.PasswordResetView.as_view(template_name="registration/password_reset_form.html", email_template_name="registration/password_reset_email.txt"), name="password_reset"),
    path("password-reset/done/", auth.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(template_name="registration/password_reset_confirm.html"), name="password_reset_confirm"),
    path("reset/done/", auth.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"), name="password_reset_complete"),
    path("account/", platform.dashboard, name="account"),
    path("account/backups/<uuid:backup_id>/", views.web_download, name="web-download"),
    path("account/backups/<uuid:backup_id>/delete/", views.delete_backup, name="delete-backup"),
    path("account/devices/<int:device_id>/revoke/", views.revoke_device, name="revoke-device"),
    path("api/v1/login/", api.login), path("api/v1/lease/", api.lease), path("api/v1/logout/", api.logout),
    path("api/v1/backups/", backups.listing), path("api/v1/backups/upload/", backups.upload),
    path("api/v1/backups/<uuid:backup_id>/", backups.download),
]
admin.site.site_header = "مدیریت اشتراک مدارس"
admin.site.site_title = "مدیریت اشتراک"


# Explicit public names keep all navigation testable and sitemap generation local.
urlpatterns += [path(name + '/', marketing.page, {'page': name}, name=name)
                for name in marketing.PUBLIC_PAGES if name != 'home']
urlpatterns += [
    path('contact/', platform.contact, name='contact'),
    path('robots.txt', marketing.robots, name='robots'), path('sitemap.xml', marketing.sitemap, name='sitemap'),
    path('account/profile/', platform.profile, name='profile'),
    path('account/security/password/', auth.PasswordChangeView.as_view(template_name='portal/password_change.html', success_url='/account/security/password/done/'), name='password_change'),
    path('account/security/password/done/', auth.PasswordChangeDoneView.as_view(template_name='portal/password_done.html'), name='password_change_done'),
    path('account/orders/', platform.orders, name='orders'),
    path('account/orders/new/<int:plan_id>/', platform.order_create, name='create-order'),
    path('account/orders/<uuid:order_id>/', platform.checkout, name='checkout'),
    path('account/orders/<uuid:order_id>/invoice/', platform.invoice, name='invoice'),
    path('account/orders/<uuid:order_id>/cancel/', platform.order_cancel, name='order-cancel'),
    path('account/tickets/', platform.tickets, name='tickets'),
    path('account/tickets/<uuid:ticket_id>/', platform.ticket, name='ticket'),
    path('account/downloads/<uuid:release_id>/', platform.release_download, name='release-download'),
    path('account/export/', platform.business_export, name='business-export'),
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
    path('owner/schools/<uuid:school_id>/', platform.owner_school, name='owner-school'),
    path('owner/schools/<uuid:school_id>/devices/<int:device_id>/revoke/', platform.owner_device_revoke, name='owner-device-revoke'),
    path('owner/health/', platform.owner_health, name='owner-health'),
]
handler400 = 'service.errors.bad_request'
handler403 = 'service.errors.forbidden'
handler404 = 'service.errors.not_found'
handler500 = 'service.errors.server_error'
