"""Real local browser verification using an isolated DB and console-email backend.

Not a production TLS, Windows, banking or mail-deliverability test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('docs/screenshots/unified-account'))
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    # dev_settings deliberately binds its public origin to this loopback address.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8000))
    report = {'scope':'synthetic local SQLite, real Django development server, console email, Chromium', 'checks':[], 'screenshots':[]}
    with tempfile.TemporaryDirectory(prefix='nxfi-unified-browser-') as directory:
        private = Path(directory)
        environment = dict(os.environ, NXFI_LOCAL_DEVELOPMENT='1', DJANGO_SETTINGS_MODULE='service.dev_settings', NXFI_DEV_DATABASE=str(private/'db.sqlite3'), PYTHONUNBUFFERED='1')
        for key in ['DATABASE_URL','SERVICE_TEST_DATABASE_URL']:
            environment.pop(key, None)
        subprocess.run([sys.executable,'-m','service.manage','migrate','--noinput'], cwd=root, env=environment, check=True, stdout=subprocess.DEVNULL)
        with (private/'server.log').open('w+') as log:
            server = subprocess.Popen([sys.executable,'-m','service.manage','runserver','127.0.0.1:8000','--noreload'], cwd=root, env=environment, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if server.poll() is not None: raise RuntimeError('Development server exited')
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:8000/login/', timeout=1): break
                    except OSError: time.sleep(.1)
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
                    context = browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='reduce'); page=context.new_page()
                    report['browser']=browser.version
                    def visit(path):
                        response=page.goto('http://127.0.0.1:8000'+path, wait_until='networkidle')
                        assert response.status==200, path
                    def capture(name):
                        filename=name+'.png';page.screenshot(path=str(args.output/filename), full_page=True);report['screenshots'].append(filename)
                    for locale in ('en','fa'):
                        page.set_viewport_size({'width':1440,'height':1000})
                        visit('/login/?next=/account/')

                        with page.expect_navigation(wait_until='networkidle'):
                            page.locator('[name=language]').select_option(locale)
                        assert page.url.endswith('/login/?next=/account/')
                        assert page.locator('html').get_attribute('dir')==('rtl' if locale=='fa' else 'ltr')
                        capture('login-'+locale)
                        for width in (1440,390):
                            page.set_viewport_size({'width':width,'height':900})
                            for path in ['/','/about/','/products/','/products/facerecognition/','/products/facerecognition/pricing/','/products/facerecognition/downloads/','/register/']:
                                visit(path)
                                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), path
                            visit('/');capture('company-'+locale+'-'+str(width))
                        report['checks'].append(locale+' language persists across pages; core routes load at 1440/390px without overflow')
                    page.set_viewport_size({'width':1440,'height':1000})
                    visit('/register/')
                    with page.expect_navigation(wait_until='networkidle'):
                        page.locator('[name=language]').select_option('en')
                    email='synthetic-unified-browser@example.invalid';password='Synthetic-Browser-518461!'
                    page.locator('[name=username]').fill(email)
                    page.locator('[name=password1]').fill(password);page.locator('[name=password2]').fill(password)
                    page.locator('main form button[type=submit]').click();page.wait_for_load_state('networkidle')
                    assert page.url.endswith('/login/')
                    log.flush();text=(private/'server.log').read_text()
                    link=re.findall(r'http://127\.0\.0\.1:8000/verify/[^\s<>]+/',text)[-1]
                    page.goto(link);page.wait_for_load_state('networkidle')
                    page.locator('[name=username]').fill(email);page.locator('[name=password]').fill(password)
                    page.locator('main form button[type=submit]').click();page.wait_for_load_state('networkidle')
                    visit('/account/');assert page.get_by_text('No product workspace yet.',exact=False).count()==1
                    capture('workspace-en-no-product')
                    page.locator('[name=school_name]').fill('Synthetic browser school')
                    page.locator('main form button[type=submit]').click();page.wait_for_load_state('networkidle')
                    assert page.url.endswith('/products/facerecognition/account/')
                    report['checks'].append('company-only registration → console verification link → login → no-product workspace → explicit school workspace creation')
                    # Recover password through the actual form and emitted token.
                    visit('/password-reset/');page.locator('[name=email]').fill(email)
                    page.locator('main form button[type=submit]').click();page.wait_for_load_state('networkidle')
                    text=(private/'server.log').read_text();link=re.findall(r'http://127\.0\.0\.1:8000/reset/[^\s<>]+/',text)[-1]
                    page.goto(link);page.wait_for_load_state('networkidle')
                    password='Synthetic-Recovered-946721!'
                    page.locator('[name=new_password1]').fill(password);page.locator('[name=new_password2]').fill(password)
                    page.locator('main form button[type=submit]').click();page.wait_for_load_state('networkidle')
                    assert page.url.endswith('/reset/done/')
                    visit('/login/');page.locator('[name=username]').fill(email);page.locator('[name=password]').fill(password)
                    page.locator('main form button[type=submit]').click();page.wait_for_load_state('networkidle')
                    assert not page.url.endswith('/login/')
                    report['checks'].append('actual password-recovery email token → new password → successful login')
                    browser.close()
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait()
    (args.output/'unified-workflow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('PASS:',len(report['checks']),'checks;',len(report['screenshots']),'screenshots')


if __name__ == '__main__': main()
