from django.utils.translation import gettext_lazy as _
from django import forms
from .portal_models import ContactRequest, SupportTicket, Release


class ContactForm(forms.ModelForm):
    consent = forms.BooleanField(label=_('I consent to use of my contact information to answer this request.'))
    class Meta:
        model = ContactRequest
        fields = ['name', 'email', 'school_name', 'kind', 'body', 'consent']
        labels = {'name': _('Your name'), 'email': _('Email'), 'school_name': _('School name'), 'kind': _('Request type'), 'body': _('Message — no student information or face images')}


class SchoolProfileForm(forms.Form):
    name = forms.CharField(label=_('School name'), max_length=160)


class TicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ['subject', 'category', 'body']
        labels = {'subject': _('Subject'), 'category': _('Category'), 'body': _('Description — do not send student information or face images')}


class ReplyForm(forms.Form):
    body = forms.CharField(label=_('Reply — no student information'), max_length=6000, widget=forms.Textarea)


class ManualPaymentForm(forms.Form):
    reference = forms.CharField(label=_('Bank payment reference'), max_length=80, min_length=6)
    acknowledge = forms.BooleanField(label=_('I understand that submitting a reference does not verify payment or activate a subscription.'))


class VerifyPaymentForm(forms.Form):
    reference = forms.CharField(label=_('Final unique bank transaction reference'), max_length=80, min_length=6)
    amount_irr = forms.IntegerField(label=_('Amount received — IRR'), min_value=1)
    confirmed = forms.BooleanField(label=_('I independently verified the amount and reference in the bank account.'))


class ReleaseForm(forms.ModelForm):
    class Meta:
        model = Release
        fields = ['product', 'version', 'platform', 'artifact_name', 'sha256', 'size_bytes', 'notes', 'is_published']
        labels = {'product': _('Product'), 'version': _('Version'), 'platform': _('Platform'), 'artifact_name': _('Artifact name already installed on the server'), 'sha256': 'SHA-256', 'size_bytes': _('Size in bytes'), 'notes': _('Release notes'), 'is_published': _('Publish in downloads')}
