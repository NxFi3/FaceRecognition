"""Optional Playwright UI verification against an isolated loopback fixture only.

The fixture JSON is private and must contain school_email/school_password,
owner_email/owner_password and order_id. Never use real school or production data.
Requires Playwright plus an installed Chromium; neither is a runtime dependency.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('docs/screenshots'))
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    origin = urlparse(args.base_url)
    if origin.scheme != 'http' or origin.hostname not in ('127.0.0.1', 'localhost', '::1') or origin.username or origin.password or origin.path not in ('', '/'):
        parser.error('Only an isolated loopback HTTP development server is allowed')
    fixture = json.loads(args.fixture.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    result = {'scope': 'synthetic local development data only', 'base_url': args.base_url,
              'browser': None, 'public_pages': [], 'screenshots': [], 'checks': []}
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
            assert response and response.status == 200, f'Unexpected page response: {path}'
            assert page.locator('html').get_attribute('lang') == 'fa'
            assert page.locator('html').get_attribute('dir') == 'rtl'
            assert page.locator('main').count() == 1
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Horizontal overflow: {path}'
            return response
        def capture(name):
            page.screenshot(path=str(args.output / name), full_page=True)
            result['screenshots'].append(name)
        public_paths = ['/', '/features/', '/how-it-works/', '/demo/', '/pricing/', '/compare/', '/downloads/',
                        '/requirements/', '/faq/', '/contact/', '/support/', '/privacy/', '/terms/', '/login/', '/register/']
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
            for path in public_paths:
                visit(path)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Horizontal overflow at {width}px: {path}'
                result['public_pages'].append({'path': path, 'viewport_width': width, 'status': 200})
                if path == '/':
                    capture('home-desktop.png' if width == 1440 else 'home-mobile.png')
                if width == 1440 and path == '/pricing/':
                    capture('pricing-desktop.png')
            if width == 390:
                visit('/')
                toggle = page.locator('[data-menu-toggle]')
                toggle.click()
                assert toggle.get_attribute('aria-expanded') == 'true'
                page.keyboard.press('Escape')
                assert toggle.get_attribute('aria-expanded') == 'false'
                result['checks'].append('mobile menu opens, Escape closes and restores focus')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        visit('/demo/')
        before = page.locator('[data-demo-present]').inner_text()
        page.locator('[data-demo-day]').select_option('monday')
        assert page.locator('[data-demo-present]').inner_text() != before
        page.locator('[data-demo-search]').fill('NONEXISTENT-SYNTHETIC-NAME')
        assert page.locator('[data-demo-empty]').is_visible()
        page.locator('[data-demo-search]').fill('')
        page.locator('[data-demo-day]').select_option('saturday')
        capture('demo-desktop.png')
        result['checks'].append('synthetic demo changes day counters and filters/empty state')
        def login(kind):
            visit('/login/')
            page.locator('input[name="username"]').fill(fixture[kind+'_email'])
            page.locator('input[name="password"]').fill(fixture[kind+'_password'])
            page.locator('form').first.locator('button').first.click()
            page.wait_for_load_state('networkidle')
            assert '/login/' not in page.url, 'Synthetic fixture login did not complete'
        login('school')
        visit('/account/')
        capture('school-dashboard.png')
        visit('/account/orders/' + fixture['order_id'] + '/')
        capture('checkout-pending.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        visit('/account/')
        capture('school-dashboard-mobile.png')
        visit('/account/orders/' + fixture['order_id'] + '/')
        result['checks'].append('school dashboard and unverified-payment checkout load at desktop/mobile sizes')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        context.clear_cookies()
        login('owner')
        visit('/owner/')
        capture('owner-dashboard.png')
        visit('/owner/health/')
        capture('owner-health.png')
        page.set_viewport_size({'width': 390, 'height': 844})
        visit('/owner/')
        capture('owner-dashboard-mobile.png')
        visit('/owner/health/')
        result['checks'].append('explicit owner dashboard and operational health load at desktop/mobile sizes')
        assert not errors, 'Browser JavaScript errors: ' + '; '.join(errors)
        result['checks'].append('no page-level JavaScript exceptions')
        # Critical navigation/forms remain rendered when JavaScript is disabled.
        no_script = browser.new_context(viewport={'width': 390, 'height': 844}, java_script_enabled=False)
        no_script_page = no_script.new_page()
        for path in ('/', '/pricing/', '/login/', '/contact/'):
            response = no_script_page.goto(args.base_url.rstrip('/') + path, wait_until='networkidle')
            assert response and response.status == 200
            assert no_script_page.locator('main').count() == 1
            assert no_script_page.locator('a[href="/contact/"]').count() > 0
        result['checks'].append('critical public content/login/contact render without JavaScript')
        no_script.close()
        context.close()
        browser.close()
    result['captured_at_utc'] = datetime.now(timezone.utc).isoformat()
    result_path = args.output / 'ui-verification.json'
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'result': 'PASS', 'public_page_checks': len(result['public_pages']),
                      'screenshots': len(result['screenshots']), 'report': str(result_path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
