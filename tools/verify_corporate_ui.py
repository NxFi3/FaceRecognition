"""Verify the company/product hierarchy against an isolated synthetic loopback fixture.

Requires optional Playwright and Chromium. Fixture credentials must be generated
for a disposable local database; no production school data or real payments.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('docs/screenshots'))
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    origin = urlparse(args.base_url)
    if (origin.scheme != 'http' or origin.hostname not in ('127.0.0.1', 'localhost', '::1')
            or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('', '/')):
        parser.error('Use an isolated loopback HTTP development server only')
    fixture = json.loads(args.fixture.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    result = {'scope': 'synthetic local company/product demonstration only', 'base_url': args.base_url,
              'browser': None, 'canonical_pages': [], 'checks': [], 'screenshots': []}
    product = '/products/facerecognition/'
    paths = ['/', '/products/', product] + [product + name + '/' for name in
        ('features', 'how-it-works', 'demo', 'pricing', 'compare', 'downloads', 'requirements', 'faq')]
    paths += ['/contact/', '/support/', '/privacy/', '/terms/', '/login/', '/register/']
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        result['browser'] = browser.version
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        def visit(path):
            response = page.goto(args.base_url.rstrip('/') + path, wait_until='networkidle')
            if not response or response.status != 200:
                raise AssertionError('Unexpected page status: ' + path)
            if page.locator('html').get_attribute('lang') != 'fa' or page.locator('html').get_attribute('dir') != 'rtl':
                raise AssertionError('Persian RTL document expected: ' + path)
            if page.locator('main').count() != 1:
                raise AssertionError('Expected one main landmark: ' + path)
            if not page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'):
                raise AssertionError('Horizontal overflow: ' + path)
            return response
        def capture(name):
            filename = 'corporate-' + name + '.png'
            page.screenshot(path=str(args.output / filename), full_page=True)
            result['screenshots'].append(filename)
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
            for path in paths:
                visit(path)
                result['canonical_pages'].append({'path': path, 'viewport_width': width, 'status': 200})
                if path == '/':
                    capture('home-desktop' if width == 1440 else 'home-mobile')
                elif path == '/products/' and width == 1440:
                    capture('products-desktop')
                elif path == product:
                    capture('facerecognition-desktop' if width == 1440 else 'facerecognition-mobile')
                elif path == product + 'pricing/' and width == 1440:
                    capture('facerecognition-pricing')
            if width == 390:
                visit('/')
                toggle = page.locator('[data-menu-toggle]')
                toggle.click()
                if toggle.get_attribute('aria-expanded') != 'true':
                    raise AssertionError('Mobile company navigation did not open')
                page.keyboard.press('Escape')
                if toggle.get_attribute('aria-expanded') != 'false':
                    raise AssertionError('Escape did not close mobile navigation')
        result['checks'].append('company, catalogue and complete first-product pages load at desktop/mobile widths')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        visit(product+'demo/')
        before = page.locator('[data-demo-present]').inner_text()
        page.locator('[data-demo-day]').select_option('monday')
        if page.locator('[data-demo-present]').inner_text() == before:
            raise AssertionError('Synthetic product demonstration did not respond')
        result['checks'].append('existing interactive FaceRecognition demo remains functional')
        visit('/login/')
        page.locator('input[name="username"]').fill(fixture['school_email'])
        page.locator('input[name="password"]').fill(fixture['school_password'])
        page.locator('form').first.locator('button').first.click()
        page.wait_for_load_state('networkidle')
        visit(product+'account/')
        capture('facerecognition-portal')
        visit(product+'account/orders/'+fixture['order_id']+'/')
        capture('facerecognition-checkout')
        result['checks'].append('FaceRecognition customer portal and pending-payment checkout work inside product scope')
        page.set_viewport_size({'width': 390, 'height': 844})
        visit(product+'account/')
        capture('facerecognition-portal-mobile')
        no_script = browser.new_context(viewport={'width': 390, 'height': 844}, java_script_enabled=False)
        no_script_page = no_script.new_page()
        for path in ('/', '/products/', product, product+'pricing/', '/login/'):
            response = no_script_page.goto(args.base_url.rstrip('/')+path, wait_until='networkidle')
            if not response or response.status != 200 or no_script_page.locator('main').count() != 1:
                raise AssertionError('Critical company/product content requires JavaScript: '+path)
        no_script.close()
        if errors:
            raise AssertionError('Browser errors: '+'; '.join(errors))
        result['checks'].append('mobile navigation/Escape, no horizontal overflow/console errors, critical no-JS rendering')
        context.close()
        browser.close()
    result['captured_at_utc'] = datetime.now(timezone.utc).isoformat()
    report = args.output / 'corporate-ui-verification.json'
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'result': 'PASS', 'canonical_page_checks': len(result['canonical_pages']),
                      'screenshots': len(result['screenshots']), 'report': str(report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
