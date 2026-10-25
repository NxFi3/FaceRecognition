from django import forms
from .portal_models import ContactRequest, SupportTicket, Release


class ContactForm(forms.ModelForm):
    consent = forms.BooleanField(label='با استفاده از اطلاعات تماس برای پاسخ به همین درخواست موافقم.')
    class Meta:
        model = ContactRequest
        fields = ['name', 'email', 'school_name', 'kind', 'body', 'consent']
        labels = {'name': 'نام شما', 'email': 'ایمیل', 'school_name': 'نام مدرسه', 'kind': 'نوع درخواست', 'body': 'پیام — بدون اطلاعات دانش‌آموز یا تصویر چهره'}


class SchoolProfileForm(forms.Form):
    name = forms.CharField(label='نام مدرسه', max_length=160)


class TicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ['subject', 'category', 'body']
        labels = {'subject': 'موضوع', 'category': 'دسته‌بندی', 'body': 'توضیحات — اطلاعات یا تصاویر چهره ارسال نکنید'}


class ReplyForm(forms.Form):
    body = forms.CharField(label='پاسخ — بدون اطلاعات دانش‌آموز', max_length=6000, widget=forms.Textarea)


class ManualPaymentForm(forms.Form):
    reference = forms.CharField(label='شناسهٔ پیگیری پرداخت بانکی', max_length=80, min_length=6)
    acknowledge = forms.BooleanField(label='می‌دانم ارسال شناسه به معنی تأیید پرداخت یا فعال‌شدن اشتراک نیست.')


class VerifyPaymentForm(forms.Form):
    reference = forms.CharField(label='شناسهٔ قطعی و یکتای تراکنش بانکی', max_length=80, min_length=6)
    amount_irr = forms.IntegerField(label='مبلغ دریافت‌شده — ریال', min_value=1)
    confirmed = forms.BooleanField(label='مبلغ و شناسه را مستقلاً در حساب بانکی کنترل کردم.')


class ReleaseForm(forms.ModelForm):
    class Meta:
        model = Release
        fields = ['version', 'platform', 'artifact_name', 'sha256', 'size_bytes', 'notes', 'is_published']
        labels = {'version': 'نسخه', 'platform': 'سکو', 'artifact_name': 'نام فایلِ از قبل نصب‌شده روی سرور', 'sha256': 'SHA-256', 'size_bytes': 'حجم بایت', 'notes': 'یادداشت انتشار', 'is_published': 'انتشار در بخش دریافت'}
