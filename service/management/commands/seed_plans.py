"""Safe editable drafts, not advertised prices or auto-activated trials."""
from django.core.management.base import BaseCommand
from service.commerce_models import Plan, FACE_PRODUCT_SLUG


class Command(BaseCommand):
    help = 'Create inactive unpublished plan drafts without overwriting existing plans/prices.'

    def handle(self, *args, **options):
        samples = [
            ('pilot-trial', 'آزمایش پایلوت', 14, 100, 1, True),
            ('school-annual', 'مدرسه · سالانه', 365, 900, 1, False),
            ('school-plus', 'مدرسه · توسعه', 365, 1500, 2, False),
        ]
        created = 0
        for slug, name, days, students, devices, trial in samples:
            _, fresh = Plan.objects.get_or_create(product_id=FACE_PRODUCT_SLUG, slug=slug, defaults={'name': name,
                'description': 'پیش‌نویس؛ پیش از انتشار، قیمت و شرایط واقعی توسط مالک تعیین شود.',
                'duration_days': days, 'student_limit': students, 'device_limit': devices,
                'is_trial': trial, 'price_irr': 0, 'is_active': False, 'is_public': False})
            created += int(fresh)
        self.stdout.write(self.style.SUCCESS(f'{created} inactive plan draft(s) created. No existing prices changed; no subscription activated.'))
