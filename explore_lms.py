"""
Login to lms.uibk.ac.at via Shibboleth SSO, then scrape course structure.
"""
from playwright.sync_api import sync_playwright
import pyotp
import time

USERNAME = "csbb8173"
PASSWORD = "5Nm!87!AcpDd9Ff"
TOTP_SECRET = "DFXLNJPKCT33THJN6N7JCSUHNTBP74KS"

def dump_inputs(page, label=""):
    inputs = page.query_selector_all("input:visible")
    print(f"\n[{label}] URL: {page.url}")
    print(f"[{label}] Title: {page.title()}")
    print(f"[{label}] Visible inputs ({len(inputs)}):")
    for i, inp in enumerate(inputs):
        print(f"  [{i}] type={inp.get_attribute('type')} id={inp.get_attribute('id')} name={inp.get_attribute('name')}")

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        ctx = browser.new_context()
        page = ctx.new_page()

        # Step 1: land on LMS
        print("Step 1: Navigate to LMS...")
        page.goto("https://lms.uibk.ac.at", wait_until="networkidle")

        # Step 2: click Universität Innsbruck "Anmelden"
        print("Step 2: Click Innsbruck SSO button...")
        anmelden = page.query_selector("text=Anmelden")
        if anmelden:
            anmelden.click()
        else:
            # fallback: click OK with dropdown
            page.click("#idpSelectListButton, button:has-text('OK'), input[value='OK']")

        page.wait_for_load_state("networkidle")
        page.screenshot(path="lms_sso_page.png")
        dump_inputs(page, "after SSO redirect")

        # Step 3: fill IdP credentials
        print("\nStep 3: Fill credentials on IdP page...")
        # Try common Shibboleth field names
        for sel in ["#username", "#j_username", "input[name='username']", "input[name='j_username']", "input[type='text']"]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                print(f"  Username field: {sel}")
                el.fill(USERNAME)
                break

        for sel in ["#password", "#j_password", "input[name='password']", "input[name='j_password']", "input[type='password']"]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                print(f"  Password field: {sel}")
                el.fill(PASSWORD)
                break

        # Submit
        for sel in ["button[type='submit']", "input[type='submit']", "#login-button"]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                print(f"  Submit: {sel}")
                el.click()
                break

        page.wait_for_load_state("networkidle")
        page.screenshot(path="lms_after_creds.png")
        dump_inputs(page, "after credentials")

        # Step 4: handle 2FA if present
        content = page.content().lower()
        if any(x in content for x in ["2fa", "totp", "one-time", "authenticator", "code", "token"]):
            print("\n2FA screen detected!")
            page.screenshot(path="lms_2fa.png")
            print("Screenshot: lms_2fa.png")
            code = pyotp.TOTP(TOTP_SECRET).now()
            print(f"  Generated TOTP code: {code}")
            for sel in ["input[type='text']", "input[name='code']", "input[name='token']", "input[name='otp']", "#code"]:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(code)
                    break
            for sel in ["button[type='submit']", "input[type='submit']"]:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    break
            page.wait_for_load_state("networkidle")

        page.screenshot(path="lms_logged_in.png")
        print(f"\nFinal URL: {page.url}")
        print(f"Final title: {page.title()}")

        # Step 5: check if logged in and explore courses
        if "dmz" not in page.url and "login" not in page.url.lower():
            print("\nLogged in! Exploring course structure...")

            # Try course overview endpoints
            for path in ["/auth/", "/auth/home", "/auth/MyCoursesSite"]:
                page.goto(f"https://lms.uibk.ac.at{path}", wait_until="networkidle")
                page.screenshot(path=f"lms_{path.replace('/','_')}.png")
                print(f"\n{path}: {page.title()}")

            print("\nWaiting 90s for manual inspection...")
        else:
            print("\nStill on login/dmz page — login may have failed.")
            print("Waiting 60s for manual inspection...")

        time.sleep(90)
        browser.close()

if __name__ == "__main__":
    run()
