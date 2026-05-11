"""Step 2: per-course change detection → data/state.json"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from auth import make_context, ensure_logged_in, BASE

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
STATE_FILE = os.path.join(DATA_DIR, 'state.json')

# Patterns that change every request — strip before hashing
_NOISE_RE = re.compile(
    r'(csrf[_-]?token|_csrf|__RequestVerificationToken'
    r'|timestamp|nonce|rand|sessionid)["\']?\s*[:=]\s*["\']?[\w\-./+=]+["\']?',
    re.IGNORECASE,
)
_TS_RE = re.compile(r'\b\d{10,13}\b')  # unix timestamps
_ONLINE_RE = re.compile(r'\(\d+ Personen sind online\)', re.IGNORECASE)


def _stable_hash(text: str) -> str:
    cleaned = _TS_RE.sub('', text)
    cleaned = _ONLINE_RE.sub('', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def check_courses(page, courses: list[dict]) -> list[dict]:
    """Return list of courses whose content hash changed since last run."""
    state = load_state()
    changed = []
    now = datetime.now(timezone.utc).isoformat()

    for course in courses:
        cid = course['id']
        page.goto(course['url'], wait_until="networkidle")
        el = page.query_selector('#o_main_center') or page.query_selector('body')
        text = el.inner_text()
        h = _stable_hash(text)

        prev = state.get(cid, {})
        prev_hash = prev.get('hash')

        if prev_hash is None:
            status = 'new'
        elif prev_hash != h:
            status = 'changed'
        else:
            status = 'unchanged'

        state[cid] = {'hash': h, 'last_run': now, 'name': course['name']}
        print(f"  [{status:9s}] {course['name'][:60]}")

        if status != 'unchanged':
            changed.append(course)

    save_state(state)
    return changed


def main():
    courses_file = os.path.join(DATA_DIR, 'courses.json')
    with open(courses_file) as f:
        courses = json.load(f)

    print(f"Checking {len(courses)} courses for changes...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = make_context(browser)
        page = ctx.new_page()
        ensure_logged_in(ctx, page)
        changed = check_courses(page, courses)
        browser.close()

    print(f"\n{len(changed)} changed, {len(courses) - len(changed)} unchanged")
    return changed


if __name__ == "__main__":
    main()
