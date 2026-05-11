"""Step 1: enumerate favorited courses → data/courses.json"""
import json
import os
import re
import sys
from playwright.sync_api import sync_playwright
from auth import make_context, ensure_logged_in, BASE

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


def get_courses(page) -> list[dict]:
    page.goto(f"{BASE}/auth/MyCoursesSite/0", wait_until="networkidle")

    # Click Bookmarks/Favoriten tab — user keeps all relevant courses favorited
    for sel in ["text=Favoriten", "text=Bookmarks", "a[href*='Bookmarks']"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            page.wait_for_load_state("networkidle")
            break

    # OLAT renders course list via AJAX after networkidle — wait for actual links
    try:
        page.wait_for_selector("a[href*='RepositoryEntry']", timeout=15000)
    except Exception:
        pass  # will fall through to empty list + saved HTML for debugging

    html = page.content()
    with open(os.path.join(DATA_DIR, 'courses_page.html'), 'w') as f:
        f.write(html)

    courses = []
    seen_ids = set()

    links = page.query_selector_all("a[href*='RepositoryEntry']")
    print(f"Found {len(links)} RepositoryEntry links in Bookmarks tab")

    for link in links:
        href = link.get_attribute("href") or ""
        m = re.search(r'RepositoryEntry/(\d+)', href)
        if not m:
            continue
        course_id = m.group(1)
        if course_id in seen_ids:
            continue
        seen_ids.add(course_id)

        name = (link.inner_text() or "").strip()
        if not name:
            name = (link.evaluate("el => el.closest('li,tr,div')?.innerText") or "").strip()
        name = re.sub(r'\s+', ' ', name)[:120]

        if not name:
            continue

        courses.append({
            "id": course_id,
            "name": name,
            "url": f"{BASE}/auth/RepositoryEntry/{course_id}",
        })

    return courses


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = make_context(browser)
        page = ctx.new_page()
        ensure_logged_in(ctx, page)
        courses = get_courses(page)
        browser.close()

    if not courses:
        print("No courses found — check data/courses_page.html for page structure")
        sys.exit(1)

    out = os.path.join(DATA_DIR, 'courses.json')
    with open(out, 'w') as f:
        json.dump(courses, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(courses)} courses to {out}:")
    for c in courses:
        print(f"  [{c['id']}] {c['name']}")


if __name__ == "__main__":
    main()
