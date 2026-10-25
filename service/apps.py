from django import apps as django_apps
from django.contrib.admin import apps as admin_apps


class OwnerAdminConfig(admin_apps.AdminConfig):
    default = False  # Selected explicitly, never as the service application's config.
    default_site = 'service.admin_site.OwnerAdminSite'


class ServiceConfig(django_apps.AppConfig):
    default = True
    name = 'service'
