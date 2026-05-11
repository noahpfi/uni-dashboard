"""
Login via Shibboleth, extract session cookies, test REST API with them.
"""
from playwright.sync_api import sync_playwright
import pyotp
import requests
import json

USERNAME = "csbb8173"
PASSWORD = "5Nm!87!AcpDd9Ff"
TOTP_SECRET = "DFXLNJPKCT33THJN6N7JCSUHNTBP74KS"
BASE = "https://lms.uibk.ac.at"

def login_and_get_cookies():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(BASE, wait_until="networkidle")
        page.screenshot(path="headless_initial.png")

        # Click Innsbruck SSO - try multiple selectors
        for sel in ["text=Anmelden", "#idpSelectListButton", "input[type='submit']", "button"]:
            el = page.query_selector(sel)
            if el:
                print(f"Clicking: {sel}")
                el.click()
                break
        page.wait_for_load_state("networkidle")

        # Fill credentials on IdP
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        # TOTP
        if page.query_selector("input[name='fudis_otp_input']"):
            code = pyotp.TOTP(TOTP_SECRET).now()
            print(f"TOTP: {code}")
            page.fill("input[name='fudis_otp_input']", code)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")

        print(f"Logged in, URL: {page.url}")

        cookies = page.context.cookies()
        browser.close()
        return cookies

def test_rest_api(cookies):
    session = requests.Session()
    for c in cookies:
        session.cookies.set(c['name'], c['value'], domain=c['domain'])

    headers = {"Accept": "application/json"}

    # Check headers from authenticated page for OLAT token
    r = session.get(BASE + "/auth/Portal/0", headers=headers)
    print(f"\nAuth portal response headers:")
    for k, v in r.headers.items():
        print(f"  {k}: {v}")

    # Check if X-OLAT-TOKEN in cookies after portal visit
    print(f"\nAll cookies after portal visit:")
    for c in session.cookies:
        print(f"  {c.name}={c.value[:30]}...")

    # Try REST with session cookies + portal visit
    endpoints = [
        "/restapi/users/me",
        "/restapi/users/me/courses",
        "/restapi/repo/courses",
    ]

    for ep in endpoints:
        r = session.get(BASE + ep, headers=headers)
        print(f"\n{ep} → {r.status_code}")
        if r.status_code == 200:
            try:
                data = r.json()
                print(json.dumps(data, indent=2)[:500])
            except:
                print(r.text[:300])
        else:
            # Check response headers for clues
            print(f"  WWW-Authenticate: {r.headers.get('WWW-Authenticate', 'none')}")

if __name__ == "__main__":
    print("Logging in...")
    cookies = login_and_get_cookies()
    print(f"Got {len(cookies)} cookies: {[c['name'] for c in cookies]}")
    print("\nTesting REST API...")
    test_rest_api(cookies)
