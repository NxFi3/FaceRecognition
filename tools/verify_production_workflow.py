"""Actual HTTPS/browser/client workflow for an explicitly disposable synthetic stack.

Never uses SERVICE_TESTING or bypasses TLS verification. Requires a privately held
fixture and the fixture CA installed in Chromium's trust store. No real payment,
email delivery or Windows vault operation is claimed. The server must use the
production settings with disposable PostgreSQL, Redis and a local STARTTLS sink.
"""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlparse
from unittest.mock import patch

import requests
from src.cloud.client import AccessDenied, ClientConfig, CloudClient, CloudError

PRODUCT = '/products/facerecognition/'
_AUDIT_STAGE = 'startup'
_AUDIT_SENSITIVE = []


def stage(name):
    global _AUDIT_STAGE
    _AUDIT_STAGE = name
    print('AUDIT_STAGE: '+name, flush=True)


def safe_error(error):
    text = str(error)
    for secret in _AUDIT_SENSITIVE:
        if secret:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'https?://[^\s<>\"\']+', '[redacted-url]', text)
    return text[:1800]



class MemoryVault:
    """Explicit test double; this does not verify Windows Credential Manager."""
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def set(self, key, value):
        self.values[key] = value
        return True
    def delete(self, key):
        self.values.pop(key, None)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verification_link(spool, recipient, origin):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        for path in sorted(spool.glob('*')):
            if not path.is_file() or path.stat().st_size > 1024*1024:
                continue
            try:
                message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
                if recipient.casefold() not in str(message.get('To', '')).casefold():
                    continue
                body = message.get_body(preferencelist=('plain',)) if message.is_multipart() else message
                text = body.get_content()
                links = re.findall(r'https://[^\s<>]+/verify/[^\s<>]+/', text)
                for link in links:
                    if link.startswith(origin+'/verify/'):
                        return link
            except (ValueError, UnicodeError, OSError):
                continue  # The sink may still be atomically publishing this message.
        time.sleep(.1)
    raise AssertionError('No verification email arrived at the synthetic local SMTP sink')


def run(args):
    fixture = json.loads(args.fixture.read_text())
    _AUDIT_SENSITIVE.extend([fixture.get('owner_password', ''), fixture.get('owner_email', '')])
    origin = fixture['base_url'].rstrip('/')
    parts = urlparse(origin)
    require(parts.scheme == 'https' and parts.hostname in ('localhost', '127.0.0.1', '::1')
            and not parts.username and not parts.password and not parts.path and not parts.query and not parts.fragment,
            'Use a disposable HTTPS loopback stack only')
    require(fixture.get('synthetic_disposable') is True, 'Fixture must explicitly attest disposable synthetic data')
    ca = Path(fixture['ca_file'])
    spool = Path(fixture['smtp_spool'])
    require(ca.is_file() and spool.is_dir(), 'CA and private SMTP spool are required')
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'actual production-settings HTTPS stack with synthetic accounts and owner-attested synthetic payments',
              'external_payment_settlement': False, 'external_email_delivery': False,
              'desktop_vault': 'memory test double, not Windows Credential Manager',
              'tls_verification_bypassed': False, 'browser': None, 'pages': [], 'checks': [], 'screenshots': []}
    suffix = secrets.token_hex(5)
    password = 'Synthetic-audit-'+secrets.token_urlsafe(18)+'!7'
    school_email = 'audit-school-'+suffix+'@example.invalid'
    other_email = 'audit-other-'+suffix+'@example.invalid'
    school_name = 'مدرسهٔ ساختگی ممیزی '+suffix
    _AUDIT_SENSITIVE.extend([password, school_email, other_email])
    session = requests.Session()
    session.trust_env = False  # Loopback only, never use a service proxy for private fixture credentials.
    def transport(method, url, **kwargs):
        return session.request(method, url, verify=str(ca), **kwargs)
    config = ClientConfig(origin, base64.b64decode(fixture['public_key'], validate=True))
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='nxfi-private-desktop-audit-') as directory, sync_playwright() as p:
        root = Path(directory)
        vault = MemoryVault()
        client = CloudClient(config, root/'cloud-state.json', store=vault, transport=transport)
        browser = p.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        report['browser'] = browser.version
        school_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        school = school_context.new_page()
        browser_errors = []
        school.on('pageerror', lambda _: browser_errors.append('pageerror'))
        def visit(page, path, status=200):
            response = page.goto(origin+path, wait_until='networkidle')
            require(response is not None and response.status == status, 'Unexpected HTTP status for '+path.split('?')[0])
            if status == 200:
                require(page.locator('html').get_attribute('lang') == 'fa' and page.locator('html').get_attribute('dir') == 'rtl', 'RTL document expected')
                require(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow on '+path)
            return response
        def capture(page, name):
            filename = 'audit-production-'+name+'.png'
            page.screenshot(path=str(args.output/filename), full_page=True)
            report['screenshots'].append(filename)
        def submit(page, form):
            form.locator('button').first.click()
            page.wait_for_load_state('networkidle')
        def login(page, email, passwd):
            visit(page, '/login/')
            page.locator('[name=username]').fill(email)
            page.locator('[name=password]').fill(passwd)
            submit(page, page.locator('form').filter(has=page.locator('[name=username]')))
            require(urlparse(page.url).path not in ('/login/', '/register/'), 'Login failed')
        def register(page, email, name):
            visit(page, '/register/')
            page.locator('[name=username]').fill(email)
            page.locator('[name=school_name]').fill(name)
            page.locator('[name=password1]').fill(password)
            page.locator('[name=password2]').fill(password)
            submit(page, page.locator('form').filter(has=page.locator('[name=password1]')))
            require(urlparse(page.url).path == '/login/', 'Registration did not complete')
            link = verification_link(spool, email, origin)
            response = page.goto(link, wait_until='networkidle')
            require(response is not None and response.status == 200 and urlparse(page.url).path == '/login/', 'Email verification failed')
            login(page, email, password)
        def denied_login():
            try:
                client.login(school_email, password)
            except AccessDenied:
                require(not client.allowed(), 'Unpaid school received desktop admission')
                return
            raise AssertionError('Unpaid/rejected school received a signed license')
        stage('public responsive pages')
        # Real static responses, responsive render and secure transport/cookie headers.
        paths = ['/', '/products/', PRODUCT] + [PRODUCT+name+'/' for name in ('features','how-it-works','demo','pricing','compare','downloads','requirements','faq')]
        paths += ['/contact/','/support/','/privacy/','/terms/','/login/','/register/']
        for width in (1440, 390):
            school.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
            for path in paths:
                response = visit(school, path)
                require('default-src' in response.headers.get('content-security-policy', ''), 'CSP missing')
                report['pages'].append({'path': path, 'width': width, 'status': response.status})
                if path in ('/', PRODUCT, '/register/'):
                    capture(school, ('company' if path == '/' else 'product' if path == PRODUCT else 'registration')+('-desktop' if width == 1440 else '-mobile'))
        stage('mobile menu')
        visit(school, '/')
        toggle = school.locator('[data-menu-toggle]')
        toggle.click()
        require(toggle.get_attribute('aria-expanded') == 'true', 'Mobile navigation did not open')
        school.keyboard.press('Escape')
        require(toggle.get_attribute('aria-expanded') == 'false', 'Escape did not close navigation')
        school.set_viewport_size({'width': 1440, 'height': 1000})
        stage('interactive demo')
        visit(school, PRODUCT+'demo/')
        before = school.locator('[data-demo-present]').inner_text()
        school.locator('[data-demo-day]').select_option('monday')
        require(school.locator('[data-demo-present]').inner_text() != before, 'Interactive synthetic demo did not update')
        school.locator('[data-demo-search]').fill('NO-SUCH-SYNTHETIC-RECORD')
        require(school.locator('[data-demo-empty]').is_visible(), 'Synthetic demo empty state missing')
        report['checks'].append('34 production-HTTPS public/auth responsive pages, mobile navigation/Escape and interactive synthetic demo/empty state')
        stage('school registration and email verification')
        register(school, school_email, school_name)
        report['checks'].append('browser registration, real local STARTTLS SMTP email receipt, email verification and school login')
        cookies = {item['name']: item for item in school_context.cookies()}
        require(cookies.get('sessionid', {}).get('secure') and cookies['sessionid']['httpOnly'], 'Secure HttpOnly session cookie absent')
        require(cookies.get('csrftoken', {}).get('secure'), 'Secure CSRF cookie absent')
        visit(school, '/owner/', status=403)
        denied_login()
        def create_order():
            visit(school, PRODUCT+'pricing/')
            form = school.locator('form[action="'+PRODUCT+'account/orders/new/'+str(fixture['plan_id'])+'/"]')
            require(form.count() == 1, 'Synthetic priced plan not available')
            submit(school, form)
            match = re.fullmatch(re.escape(PRODUCT)+r'account/orders/([0-9a-f-]+)/', urlparse(school.url).path)
            require(match is not None, 'Checkout was not created')
            return match.group(1)
        stage('unpaid checkout and claim')
        first_order = create_order()
        school.locator('[name=reference]').fill('BANK-AUDIT-CLAIM-'+suffix.upper())
        school.locator('[name=acknowledge]').check()
        submit(school, school.locator('form').filter(has=school.locator('[name=reference]')))
        capture(school, 'checkout-unverified')
        denied_login()
        report['checks'].append('priced checkout and manual evidence claim do not activate a subscription')
        stage('second school and tenant denial')
        other_context = browser.new_context(viewport={'width': 390, 'height': 844})
        other = other_context.new_page()
        register(other, other_email, 'مدرسهٔ ساختگی دوم ممیزی')
        visit(other, PRODUCT+'account/orders/'+first_order+'/invoice/', status=404)
        visit(other, '/owner/', status=403)
        other_context.close()
        report['checks'].append('school accounts denied owner console and another school invoice')
        stage('owner login and payment rejection')
        owner_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        owner = owner_context.new_page()
        login(owner, fixture['owner_email'], fixture['owner_password'])
        def owner_payment(order_id):
            visit(owner, '/owner/orders/'+order_id+'/verify/')
            if urlparse(owner.url).path == '/owner/reauth/':
                owner.locator('[name=password]').fill(fixture['owner_password'])
                submit(owner, owner.locator('form').filter(has=owner.locator('[name=password]')))
            require(urlparse(owner.url).path.endswith('/verify/'), 'Owner step-up failed')
        owner_payment(first_order)
        owner.locator('[name=reason]').fill('Synthetic audit: settlement not found; this is not an actual payment.')
        submit(owner, owner.locator('form[action$="/reject/"]'))
        denied_login()
        visit(school, PRODUCT+'account/orders/'+first_order+'/')
        require(school.locator('[name=reference]').count() == 0, 'Rejected checkout remained payable')
        report['checks'].append('owner rejection preserves inactive entitlement and rejects subsequent payment claims')
        stage('payment wrong amount and synthetic owner approval')
        order = create_order()
        school.locator('[name=reference]').fill('BANK-AUDIT-VERIFIED-'+suffix.upper())
        school.locator('[name=acknowledge]').check()
        submit(school, school.locator('form').filter(has=school.locator('[name=reference]')))
        owner_payment(order)
        owner.locator('[name=reference]').fill('BANK-AUDIT-VERIFIED-'+suffix.upper())
        owner.locator('[name=amount_irr]').fill(str(int(fixture['price_irr'])+1))
        owner.locator('[name=confirmed]').check()
        submit(owner, owner.locator('form').filter(has=owner.locator('[name=amount_irr]')))
        denied_login()
        require(urlparse(owner.url).path.endswith('/verify/'), 'Wrong payment amount unexpectedly fulfilled')
        capture(owner, 'owner-payment-validation-error')
        owner.locator('[name=amount_irr]').fill(str(fixture['price_irr']))
        owner.locator('[name=confirmed]').check()
        submit(owner, owner.locator('form').filter(has=owner.locator('[name=amount_irr]')))
        require(urlparse(owner.url).path == '/owner/', 'Synthetic owner attestation failed')
        capture(owner, 'owner-verified')
        stage('desktop signed activation and device limit')
        lease = client.login(school_email, password)
        require(client.allowed() and lease['student_limit'] == fixture.get('student_limit', 900), 'Correct signed entitlement not available')
        require(lease['device_limit'] == fixture.get('device_limit', 1), 'Device entitlement mismatch')
        report['checks'].append('wrong amount cannot activate; synthetic owner attestation activates actual pinned-Ed25519 desktop client over verified HTTPS')
        # Actual configured device allowance, rather than a manufactured lease fixture.
        if lease['device_limit'] == 1:
            extra = CloudClient(config, root/'extra-device.json', store=MemoryVault(), transport=transport)
            try:
                extra.login(school_email, password)
                raise AssertionError('Device limit bypassed')
            except AccessDenied:
                require(not extra.allowed(), 'Extra device admitted')
            report['checks'].append('actual server device-limit enforcement rejects second installation')
        stage('encrypted HTTPS backup and full restore')
        from src import database
        db = root/'attendance.db'
        with patch.object(database, 'DB_PATH', db):
            database.init_db()
            database.add_student('SYNTHETIC-AUDIT-1', 'دادهٔ ساختگی؛ دانش‌آموز واقعی نیست')
        client.bind_database(db)
        photos = root/'early_exit_review'
        photos.mkdir()
        (photos/'event_1.jpg').write_bytes(b'synthetic audit evidence, not an actual student image')
        client.configure_backup_key(root/'school.frkey')
        uploaded = client.backup(db)
        require(uploaded.get('format') == 'client-v2', 'Server did not record a client-encrypted backup format')
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("INSERT INTO students(student_code,name) VALUES('LATER-AUDIT','Synthetic later record')")
        (photos/'event_1.jpg').write_bytes(b'later synthetic evidence')
        client.restore(uploaded['id'], db)
        with closing(sqlite3.connect(db)) as connection:
            require(connection.execute('SELECT COUNT(*) FROM students').fetchone()[0] == 1, 'Restored DB mismatch')
        require((photos/'event_1.jpg').read_bytes().startswith(b'synthetic audit evidence'), 'Restored photo mismatch')
        report['checks'].append('real HTTPS client-encrypted backup upload/download/full SQLite+photo restore')
        def unavailable(*args, **kwargs):
            raise requests.ConnectionError('synthetic network outage')
        stage('offline restart and server outage')
        proxy = fixture.get('audit_proxy_container')
        if proxy:
            require(isinstance(proxy, str) and re.fullmatch(r'nxfi-audit-[A-Za-z0-9_-]{1,80}-tls', proxy) is not None,
                    'Outage control must name only the dedicated disposable audit TLS proxy')
            subprocess.run(['docker','container','stop',proxy], check=True, capture_output=True, timeout=30)
            try:
                offline = CloudClient(config, root/'cloud-state.json', store=vault, transport=transport)
                require(offline.allowed(), 'Valid offline startup blocked during actual proxy outage')
                try:
                    offline.renew()
                    raise AssertionError('Proxy outage unexpectedly returned a response')
                except CloudError:
                    require(offline.allowed(), 'Actual HTTPS outage invalidated paid admission')
                offline.close()
            finally:
                subprocess.run(['docker','container','start',proxy], check=True, capture_output=True, timeout=30)
            deadline = time.monotonic()+20
            while True:
                try:
                    recovered = session.get(origin+'/health/', verify=str(ca), timeout=(1,2)).status_code == 200
                except requests.RequestException:
                    recovered = False
                if recovered:
                    break
                require(time.monotonic() < deadline, 'HTTPS proxy did not recover after actual outage test')
                time.sleep(.2)
            client.renew()
            require(client.allowed(), 'Recovered HTTPS synchronization failed')
            report['checks'].append('actual dedicated TLS proxy stop/start: offline startup and failed sync preserve paid license, verified HTTPS sync recovers')
        else:
            offline = CloudClient(config, root/'cloud-state.json', store=vault, transport=unavailable)
            require(offline.allowed(), 'Valid offline startup blocked')
            try:
                offline.renew()
            except CloudError:
                pass
            require(offline.allowed(), 'Network failure invalidated valid paid license')
            offline.close()
            report['checks'].append('desktop offline restart and injected connection failure preserve valid paid admission (no actual server-stop claim)')
        stage('expiration and renewal')
        visit(owner, '/owner/')
        customer_links = owner.locator('a[href^="/owner/schools/"]').evaluate_all('(links) => links.map(a => ({href:a.getAttribute("href"), text:a.parentElement.textContent}))')
        school_path = next((link['href'] for link in customer_links if school_name in link['text']), None)
        require(school_path is not None, 'Owner customer editor unavailable')
        def update_period(start, end):
            visit(owner, school_path)
            if urlparse(owner.url).path == '/owner/reauth/':
                owner.locator('[name=password]').fill(fixture['owner_password'])
                submit(owner, owner.locator('form').filter(has=owner.locator('[name=password]')))
            owner.locator('[name=starts_at]').fill(start.strftime('%Y-%m-%d %H:%M:%S'))
            owner.locator('[name=expires_at]').fill(end.strftime('%Y-%m-%d %H:%M:%S'))
            submit(owner, owner.locator('form').filter(has=owner.locator('[name=expires_at]')))
            require(owner.locator('.errorlist').count() == 0, 'Owner period update rejected')
        now = datetime.now(timezone.utc)
        update_period(now-timedelta(days=400), now-timedelta(days=1))
        try:
            client.renew()
            raise AssertionError('Expired server subscription admitted')
        except AccessDenied:
            require(not client.allowed(), 'Expired signed status did not stop admission')
        update_period(now-timedelta(days=1), now+timedelta(days=365))
        client.renew()
        require(client.allowed(), 'Online renewal did not recover expired admission')
        renewed = CloudClient(config, root/'cloud-state.json', store=vault, transport=unavailable)
        require(renewed.allowed(), 'Renewed license could not start offline')
        report['checks'].append('actual owner expiration and online renewal recover admission without deleting local data')
        visit(school, PRODUCT+'account/')
        capture(school, 'school-active')
        school.set_viewport_size({'width': 390, 'height': 844})
        visit(school, PRODUCT+'account/')
        capture(school, 'school-mobile')
        stage('device revocation and durable offline denial')
        revoke = school.locator('form[action*="/devices/"][action$="/revoke/"]')
        require(revoke.count() == 1, 'Activated device not present in portal')
        submit(school, revoke)
        try:
            client.renew()
            raise AssertionError('Revoked installation received admission')
        except AccessDenied:
            require(not client.allowed(), 'Signed revocation did not stop current admission')
        require(not CloudClient(config, root/'cloud-state.json', store=vault, transport=unavailable).allowed(), 'Revocation not durable across offline restart')
        report['checks'].append('customer device revocation becomes durable after next successful authenticated sync')
        stage('revoked credential logout and stale bearer rejection')
        old_credential = client.credential
        require(isinstance(old_credential, str) and bool(old_credential), 'Revoked installation credential unavailable for logout test')
        client.logout()
        require(not client.allowed(), 'Local logout did not clear admission')
        with transport('POST', origin+'/api/v1/lease/',
                       headers={'Authorization': 'Bearer '+old_credential},
                       json={'nonce': secrets.token_urlsafe(32), 'license_version': 2},
                       timeout=(3,10), allow_redirects=False, stream=True) as old_response:
            require(old_response.status_code == 401, 'Signed-status credential remained usable after logout')
        require(not CloudClient(config, root/'cloud-state.json', store=vault, transport=unavailable).allowed(),
                'Logout after revocation was not durable across offline restart')
        report['checks'].append('actual HTTPS logout accepts the current revoked bearer solely for invalidation; old bearer lease sync returns 401 and offline restart stays denied')
        require(db.exists(), 'License lifecycle deleted school-owned local data')
        require(not browser_errors, 'Page-level browser exception')
        owner_context.close()
        school_context.close()
        browser.close()
    report['captured_at_utc'] = datetime.now(timezone.utc).isoformat()
    (args.output/'audit-production-workflow.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'result': 'PASS', 'checks': len(report['checks']), 'page_checks': len(report['pages']), 'screenshots': len(report['screenshots'])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('docs/screenshots'))
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        # Browser/network exceptions may contain verification links; do not print them.
        print('FAIL: stage='+_AUDIT_STAGE+'; '+type(exc).__name__+': '+safe_error(exc))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
