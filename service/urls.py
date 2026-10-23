from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from django.views.generic import RedirectView
from . import api, backups, views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="account", permanent=False)),
    path("admin/", admin.site.urls), path("health/", views.health),
    path("register/", views.register, name="register"), path("verify/<str:token>/", views.verify, name="verify"),
    path("resend-verification/", views.resend, name="resend"),
    path("login/", views.SchoolLoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password-reset/", auth.PasswordResetView.as_view(template_name="registration/password_reset_form.html", email_template_name="registration/password_reset_email.txt"), name="password_reset"),
    path("password-reset/done/", auth.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(template_name="registration/password_reset_confirm.html"), name="password_reset_confirm"),
    path("reset/done/", auth.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"), name="password_reset_complete"),
    path("account/", views.account, name="account"),
    path("account/backups/<uuid:backup_id>/", views.web_download, name="web-download"),
    path("account/backups/<uuid:backup_id>/delete/", views.delete_backup, name="delete-backup"),
    path("account/devices/<int:device_id>/revoke/", views.revoke_device, name="revoke-device"),
    path("api/v1/login/", api.login), path("api/v1/lease/", api.lease), path("api/v1/logout/", api.logout),
    path("api/v1/backups/", backups.listing), path("api/v1/backups/upload/", backups.upload),
    path("api/v1/backups/<uuid:backup_id>/", backups.download),
]
admin.site.site_header = "مدیریت اشتراک مدارس"
admin.site.site_title = "مدیریت اشتراک"
