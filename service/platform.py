"""Tenant-scoped commercial portal. No student or inference data enters this module."""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.core.paginator import Paginator
from django.http import Http404, FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import School, Device, Backup, AuditEvent
from .portal_models import ContactRequest, SupportTicket, TicketReply, Release, EmailOutbox
from .platform_forms import (ContactForm, SchoolProfileForm, TicketForm, ReplyForm,
    ManualPaymentForm, VerifyPaymentForm, ReleaseForm)
from .security import throttled, too_many
from .owner_auth import owner_required, is_company_owner
from .commerce_models import Plan, Order, VerifiedPayment
from .commerce import create_order, submit_manual_payment, verify_manual_payment, approve_trial, cancel_order, reject_manual_payment


def school_for(request):
    return get_object_or_404(School, user=request.user)


def notify(recipient, subject, body, key):
    EmailOutbox.objects.get_or_create(dedupe_key=key, defaults={'recipient': recipient,
        'subject': subject, 'body': body, 'available_at': timezone.now()})


def contact(request):
    form = ContactForm(request.POST or None, initial={'kind': 'demo' if request.GET.get('demo') or request.GET.get('kind') == 'demo' else 'contact'})
    if request.method == 'POST':
        if throttled(request, 'contact', request.POST.get('email', '')[:254], limit=5, seconds=3600):
            return too_many()
        if form.is_valid():
            with transaction.atomic():
                entry = form.save()
                notify(entry.email, 'درخواست شما در NxFi ثبت شد', 'درخواست شما ثبت شده است. این پیام به معنی تأیید خرید یا فعال‌سازی اشتراک نیست. لطفاً اطلاعات دانش‌آموز یا چهره ارسال نکنید.', 'contact:'+str(entry.pk))
            messages.success(request, 'درخواست ثبت شد. پاسخ از طریق ایمیل پیگیری می‌شود.')
            return redirect('contact')
    return render(request, 'marketing/contact.html', {'form': form, 'title': 'تماس و درخواست نمایش', 'description': 'گفت‌وگو دربارهٔ پایلوت و نیازهای مدرسه', 'canonical_url': settings.PUBLIC_URL+reverse('contact'), 'indexable': True, 'page_title': 'تماس و درخواست نمایش', 'meta_description': 'ارتباط با تیم NxFi برای بررسی پایلوت مدرسه'})


@login_required
def dashboard(request):
    if is_company_owner(request.user):
        return redirect('owner')
    school = school_for(request)
    return render(request, 'portal/dashboard.html', {'school': school,
        'devices': Device.objects.filter(school=school), 'backups': Backup.objects.filter(school=school),
        'orders': Order.objects.filter(school=school).order_by('-created_at')[:8],
        'active_device_count': Device.objects.filter(school=school, revoked=False, token_digest__isnull=False).count(),
        'ticket_count': SupportTicket.objects.filter(school=school, status='open').count()})


@login_required
def profile(request):
    school = school_for(request)
    form = SchoolProfileForm(request.POST or None, initial={'name': school.name})
    if request.method == 'POST' and form.is_valid():
        school.name = form.cleaned_data['name']
        school.save(update_fields=['name'])
        AuditEvent.objects.create(actor=request.user, school=school, action='school.profile.updated', object_id=str(school.pk))
        messages.success(request, 'نام مدرسه به‌روز شد؛ تغییر ایمیل از مسیر پشتیبانی انجام می‌شود.')
        return redirect('profile')
    return render(request, 'portal/form.html', {'title': 'مشخصات مدرسه', 'form': form, 'school': school})


@login_required
def orders(request):
    return render(request, 'portal/orders.html', {'orders': Paginator(Order.objects.filter(school=school_for(request)).order_by('-created_at'), 25).get_page(request.GET.get('page'))})


@login_required
@require_POST
def order_create(request, plan_id):
    school = school_for(request)
    if throttled(request, 'order-create', str(school.pk), limit=20):
        return too_many()
    try:
        order = create_order(school=school, plan_id=plan_id, idempotency_key=request.POST.get('idempotency_key', ''))
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect('pricing')
    return redirect('checkout', order_id=order.pk)


@login_required
def checkout(request, order_id):
    order = get_object_or_404(Order, pk=order_id, school=school_for(request))
    form = ManualPaymentForm(request.POST or None)
    if request.method == 'POST':
        if throttled(request, 'payment-submit', str(order.school_id), limit=10):
            return too_many()
        if form.is_valid():
            try:
                submit_manual_payment(school=order.school, order_id=order.pk, reference=form.cleaned_data['reference'])
            except (ValidationError, ValueError) as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'شناسه ثبت شد؛ اشتراک فقط پس از تأیید مستقل مالک فعال می‌شود.')
                return redirect('checkout', order_id=order.pk)
    return render(request, 'portal/checkout.html', {'order': order, 'form': form,
        'instructions': settings.MANUAL_PAYMENT_INSTRUCTIONS})


@login_required
@require_POST
def order_cancel(request, order_id):
    try:
        cancel_order(school=school_for(request), order_id=order_id)
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect('orders')


@login_required
def invoice(request, order_id):
    order = get_object_or_404(Order, pk=order_id, school=school_for(request))
    return render(request, 'portal/invoice.html', {'order': order,
        'payment': VerifiedPayment.objects.filter(order=order).first()})


@login_required
def tickets(request):
    school = school_for(request)
    form = TicketForm(request.POST or None, initial={'category': request.GET.get('category', 'support')})
    if request.method == 'POST':
        if throttled(request, 'ticket-create', str(school.pk), limit=10, seconds=3600):
            return too_many()
        if form.is_valid():
            with transaction.atomic():
                item = form.save(commit=False)
                item.school = school
                item.save()
                AuditEvent.objects.create(actor=request.user, school=school, action='ticket.created', object_id=str(item.pk))
            return redirect('ticket', ticket_id=item.pk)
    return render(request, 'portal/tickets.html', {'tickets': Paginator(SupportTicket.objects.filter(school=school), 25).get_page(request.GET.get('page')), 'form': form})


@login_required
def ticket(request, ticket_id):
    item = get_object_or_404(SupportTicket, pk=ticket_id, school=school_for(request))
    form = ReplyForm(request.POST or None)
    if request.method == 'POST':
        if throttled(request, 'ticket-reply', str(item.school_id), limit=30):
            return too_many()
        if form.is_valid():
            with transaction.atomic():
                item = SupportTicket.objects.select_for_update().get(pk=item.pk, school=item.school)
                TicketReply.objects.create(ticket=item, author=request.user, body=form.cleaned_data['body'])
                item.status = 'open'
                item.save(update_fields=['status', 'updated_at'])
            return redirect('ticket', ticket_id=item.pk)
    return render(request, 'portal/ticket.html', {'ticket': item, 'form': form})


def checked_artifact(release):
    """Use a single verified handle: no URL fetching, uploads, traversal or reopen race."""
    release.full_clean()
    root = Path(settings.RELEASE_ROOT).resolve()
    path = root / release.artifact_name
    if path.resolve().parent != root or path.is_symlink():
        raise ValidationError('مسیر فایل انتشار مجاز نیست.')
    stream = path.open('rb')
    try:
        digest, size = hashlib.sha256(), 0
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
            size += len(chunk)
        if size != release.size_bytes or digest.hexdigest() != release.sha256:
            raise ValidationError('حجم یا SHA-256 فایل با ثبت انتشار مطابقت ندارد.')
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


@login_required
def release_download(request, release_id):
    school = school_for(request)  # Expired owners may download recovery software too.
    release = get_object_or_404(Release, pk=release_id, is_published=True)
    if throttled(request, 'release-download', str(school.pk), limit=10, seconds=3600):
        return too_many()
    try:
        stream = checked_artifact(release)
    except (OSError, ValidationError):
        return render(request, 'portal/unavailable.html', status=503)
    AuditEvent.objects.create(actor=request.user, school=school, action='release.downloaded', object_id=str(release.pk))
    response = FileResponse(stream, as_attachment=True, filename=release.artifact_name, content_type='application/octet-stream')
    response['X-Content-SHA256'] = release.sha256
    return response


@login_required
def business_export(request):
    school = school_for(request)
    if throttled(request, 'business-export', str(school.pk), limit=4, seconds=3600):
        return too_many()
    payload = {'school': {'id': str(school.pk), 'name': school.name, 'email': request.user.email},
        'orders': list(Order.objects.filter(school=school).values('id', 'status', 'amount_irr', 'created_at')),
        'tickets': list(SupportTicket.objects.filter(school=school).values('id','subject','category','body','status','created_at')),
        'replies': list(TicketReply.objects.filter(ticket__school=school).values('ticket_id','body','created_at'))}
    from django.core.serializers.json import DjangoJSONEncoder
    response = HttpResponse(json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="school-business-records.json"'
    return response


@owner_required()
def owner(request):
    totals = VerifiedPayment.objects.aggregate(total=Sum('amount_irr'))
    # No license API/client keys are exposed in this overview.
    return render(request, 'owner/dashboard.html', {'revenue': totals['total'] or 0,
        'schools': School.objects.select_related('user').order_by('-created_at')[:100],
        'orders': Order.objects.select_related('school').order_by('-created_at')[:100],
        'plans': Plan.objects.order_by('sort_order'), 'contacts': ContactRequest.objects.order_by('-created_at')[:50],
        'tickets': SupportTicket.objects.select_related('school').all()[:50],
        'releases': Release.objects.all()[:20], 'audits': AuditEvent.objects.select_related('actor').all()[:50]})


@owner_required(recent=True)
def owner_payment(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    form = VerifyPaymentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            verify_manual_payment(order_id=order.pk, actor=request.user,
                reference=form.cleaned_data['reference'], amount_irr=form.cleaned_data['amount_irr'])
        except (ValidationError, ValueError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, 'پرداخت تأیید و اشتراک به‌صورت اتمی اعمال شد.')
            return redirect('owner')
    return render(request, 'owner/payment.html', {'order': order, 'form': form})


@owner_required(recent=True)
@require_POST
def owner_trial(request, order_id):
    try:
        approve_trial(order_id=order_id, actor=request.user)
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect('owner')


@owner_required(recent=True)
def owner_plan(request, plan_id=None):
    from django.forms import modelform_factory
    form_class = modelform_factory(Plan, fields=['slug','name','description','price_irr','duration_days','student_limit','device_limit','is_trial','is_active','is_public','sort_order'])
    item = get_object_or_404(Plan, pk=plan_id) if plan_id else None
    form = form_class(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        AuditEvent.objects.create(actor=request.user, action='plan.updated', object_id=str(item.pk))
        messages.success(request, 'طرح ذخیره شد؛ سفارش‌های قبلی مبلغ ثبت‌شدهٔ خود را نگه می‌دارند.')
        return redirect('owner')
    return render(request, 'portal/form.html', {'title': 'تعریف / ویرایش طرح', 'form': form})


@owner_required(recent=True)
def owner_release(request):
    form = ReleaseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        try:
            with checked_artifact(item):
                pass
        except (OSError, ValidationError) as exc:
            form.add_error(None, str(exc))
        else:
            item.save()
            AuditEvent.objects.create(actor=request.user, action='release.published' if item.is_published else 'release.registered', object_id=str(item.pk))
            return redirect('owner')
    return render(request, 'portal/form.html', {'title': 'ثبت انتشار — فایل باید توسط اپراتور روی سرور نصب شده باشد', 'form': form})


@owner_required(recent=True)
@require_POST
def owner_release_withdraw(request, release_id):
    item = get_object_or_404(Release, pk=release_id)
    item.is_published = False
    item.save(update_fields=['is_published'])
    AuditEvent.objects.create(actor=request.user, action='release.withdrawn', object_id=str(item.pk))
    return redirect('owner')


@owner_required(recent=True)
def owner_ticket(request, ticket_id):
    item = get_object_or_404(SupportTicket, pk=ticket_id)
    form = ReplyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            item = SupportTicket.objects.select_for_update().get(pk=item.pk)
            reply = TicketReply.objects.create(ticket=item, author=request.user, body=form.cleaned_data['body'])
            item.status = 'closed' if request.POST.get('close') == 'yes' else 'open'
            item.save(update_fields=['status','updated_at'])
            notify(item.school.user.email, 'پاسخ تیکت NxFi', 'پاسخ درخواست شما در پرتال امن مدرسه قابل مشاهده است.', 'ticket-reply:'+str(reply.pk))
            AuditEvent.objects.create(actor=request.user, school=item.school, action='ticket.replied', object_id=str(item.pk))
        return redirect('owner-ticket', ticket_id=item.pk)
    return render(request, 'portal/ticket.html', {'ticket': item, 'form': form, 'owner_view': True})


@owner_required(recent=True)
@require_POST
def owner_contact(request, contact_id):
    item = get_object_or_404(ContactRequest, pk=contact_id)
    item.handled = True
    item.save(update_fields=['handled'])
    AuditEvent.objects.create(actor=request.user, action='contact.handled', object_id=str(item.pk))
    return redirect('owner')


@owner_required()
def owner_health(request):
    from django.db import connection
    from django.core.cache import cache
    health = {'database': False, 'cache': False, 'email_pending': EmailOutbox.objects.filter(sent_at__isnull=True).count()}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        health['database'] = True
        cache.set('owner-health', True, 5)
        health['cache'] = cache.get('owner-health') is True
    except Exception:
        health['probe_error'] = True
    return render(request, 'owner/health.html', {'health': health})


@owner_required(recent=True)
@require_POST
def owner_reject_payment(request, order_id):
    try:
        reject_manual_payment(order_id=order_id, actor=request.user, reason=request.POST.get('reason', ''))
    except Order.DoesNotExist:
        raise Http404
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'ادعای پرداخت رد شد؛ هیچ پرداخت یا اشتراکی فعال نشد.')
    return redirect('owner-payment', order_id=order_id)


@owner_required(recent=True)
def owner_school(request, school_id):
    from .admin import SchoolAdminForm
    class SchoolCommercialForm(SchoolAdminForm):
        class Meta(SchoolAdminForm.Meta):
            fields = ['name','status','starts_at','expires_at','student_limit','device_limit','backups_enabled','backup_quota_bytes','backup_retention']
    school = get_object_or_404(School.objects.select_related('user'), pk=school_id)
    form = SchoolCommercialForm(request.POST or None, instance=school)
    if request.method == 'POST':
        # Keep the form's School row lock until the complete update is durable.
        with transaction.atomic():
            if form.is_valid():
                form.save()
                AuditEvent.objects.create(actor=request.user, school=school, action='subscription.owner_updated', object_id=str(school.pk))
                messages.success(request, 'تنظیم مدرسه ذخیره شد؛ این تغییر دستی، پرداخت یا درآمدی ثبت نمی‌کند.')
                return redirect('owner-school', school_id=school.pk)
    return render(request, 'owner/school.html', {'school': school, 'form': form,
        'devices': Device.objects.filter(school=school)})


@owner_required(recent=True)
@require_POST
def owner_device_revoke(request, school_id, device_id):
    with transaction.atomic():
        school = get_object_or_404(School.objects.select_for_update(), pk=school_id)
        device = get_object_or_404(Device, pk=device_id, school=school)
        device.revoked = True
        device.save(update_fields=['revoked'])  # Digest retained for signed revocation sync.
        AuditEvent.objects.create(actor=request.user, school=school, action='device.owner_revoked', object_id=str(device.pk))
    return redirect('owner-school', school_id=school.pk)
