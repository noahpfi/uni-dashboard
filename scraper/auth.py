import os
import time
import pyotp
from playwright.sync_api import Page
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

BASE = "https://lms.uibk.ac.at"
USER = os.environ["LMS_USER"]
PASSWORD = os.environ["LMS_PASSWORD"]
TOTP_SECRET = os.environ["LMS_TOTP_SECRET"]


def login(page: Page) -> None:
    page.goto(BASE, wait_until="networkidle")

    # IdP selector — may already be past this if IdP session active
    btn = page.query_selector("#idpSelectListButton")
    if btn:
        btn.click()
        page.wait_for_load_state("networkidle")

    # Credentials step — skipped if IdP session still active
    if page.query_selector("#username"):
        page.fill("#username", USER)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

    # TOTP step
    if page.query_selector("input[name='fudis_otp_input']"):
        totp = pyotp.TOTP(TOTP_SECRET)
        # wait until at least 5 s remain in current window to avoid submitting
        # a code that expires before the server validates it
        remaining = 30 - (int(time.time()) % 30)
        if remaining < 5:
            time.sleep(remaining + 1)
        code = totp.now()
        page.fill("input[name='fudis_otp_input']", code)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

    if "/auth/" not in page.url:
        raise RuntimeError(f"Login failed, ended on: {page.url}")

    print(f"Logged in: {page.url}")


SESSION_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'session.json')


def make_context(browser):
    """Return a browser context, restoring saved session cookies if available."""
    if os.path.exists(SESSION_FILE):
        try:
            return browser.new_context(storage_state=SESSION_FILE)
        except Exception:
            pass
    return browser.new_context()


def ensure_logged_in(context, page: Page) -> None:
    """Use saved session if valid; otherwise full login + save session."""
    page.goto(f"{BASE}/auth/MyCoursesSite/0", wait_until="networkidle")
    # OLAT serves /auth/ URL even when unauthenticated — body gets class "o_dmz"
    # when not logged in, "o_auth" when session is valid.
    body_class = page.evaluate("document.body.className")
    if "/auth/" in page.url and "o_dmz" not in body_class:
        print(f"Logged in (session): {page.url}")
        return
    print("Session expired or missing, logging in...")
    login(page)
    context.storage_state(path=SESSION_FILE)
    print("Session saved.")
