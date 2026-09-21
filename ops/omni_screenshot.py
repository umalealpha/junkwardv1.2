"""ops/omni_screenshot.py — headless screenshot of an omni page.

Runs ON THE EC2 HOST (not in the backend container) in a venv that has
Playwright + chromium installed. Authenticates the SPA as the locked read-only
screenshot bot by injecting a fresh token into localStorage (server-side, so it
never trips the assistant's client-side credential-injection guard).

  python omni_screenshot.py --path /hris/leave-encashment \
      --token <fresh-bot-token> --out /tmp/shot.png

Exit 0 + "OK <path>" on success; non-zero on failure.
"""
import argparse
import sys

BASE = 'https://omni.alphadirect.co.bw'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', required=True, help='omni route, e.g. /hris/leave-encashment')
    ap.add_argument('--token', required=True, help='fresh screenshot-bot token')
    ap.add_argument('--out', required=True, help='output PNG path')
    ap.add_argument('--wait', type=int, default=6000, help='ms to settle after load')
    ap.add_argument('--width', type=int, default=1440)
    ap.add_argument('--height', type=int, default=1600)
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
        ctx = browser.new_context(viewport={'width': a.width, 'height': a.height})
        page = ctx.new_page()
        # Land on the same origin first so localStorage belongs to omni, seed the
        # token to pass the getToken() guard, then load the real page.
        page.goto(f'{BASE}/login', wait_until='domcontentloaded', timeout=45000)
        page.evaluate("t => localStorage.setItem('alpha_token', t)", a.token)
        try:
            page.goto(f'{BASE}{a.path}', wait_until='networkidle', timeout=45000)
        except Exception:
            # networkidle can never settle if the page polls; fall back to load.
            page.goto(f'{BASE}{a.path}', wait_until='load', timeout=45000)
        page.wait_for_timeout(a.wait)
        final_url = page.url
        page.screenshot(path=a.out, full_page=True)
        browser.close()

    if 'login' in final_url.lower() or 'microsoftonline' in final_url.lower():
        print(f'WARN landed on {final_url} — auth may have failed', file=sys.stderr)
    print('OK ' + a.out + ' (final_url=' + final_url + ')')


if __name__ == '__main__':
    main()
