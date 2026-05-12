"""Step 3: extract Mitteilungen per course → data/courses/<id>/mitteilungen.json"""
import json
import os
import re
import sys
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from auth import make_context, ensure_logged_in, BASE

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
STATE_FILE = os.path.join(DATA_DIR, 'state.json')
CHANGED_FILE = os.path.join(DATA_DIR, 'changed_courses.json')

_MONTH_DE = {
    'JAN': 1, 'FEB': 2, 'MÄR': 3, 'APR': 4, 'MAI': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OKT': 10, 'NOV': 11, 'DEZ': 12,
}


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_changed() -> set[str] | None:
    """Return set of changed course IDs, or None if no change info (run all)."""
    try:
        with open(CHANGED_FILE) as f:
            return set(json.load(f))
    except Exception:
        return None


def _parse_datecomp(dc_div) -> str | None:
    try:
        year = dc_div.select_one('[class*=o_year] span').get_text(strip=True)
        month_abbr = dc_div.select_one('[class*=o_month] span').get_text(strip=True)
        day = dc_div.select_one('[class*=o_day] span').get_text(strip=True)
        m = _MONTH_DE.get(month_abbr[:3].upper(), 0)
        if m:
            return f"{year}-{m:02d}-{int(day):02d}"
    except Exception:
        pass
    return None


def _find_mitteilungen_url(page) -> str | None:
    link = page.query_selector('a.o_peekview_infomsg_link')
    if link:
        return link.get_attribute('href')
    return None


def _parse_entries(soup) -> list[dict]:
    entries = []
    for msg in soup.select('.o_msg.o_block_large'):
        dc = msg.select_one('.o_datecomp')
        date_str = _parse_datecomp(dc) if dc else None

        title_el = msg.select_one('h3.o_title')
        title = title_el.get_text(strip=True) if title_el else ''

        meta_el = msg.select_one('.o_meta')
        meta = meta_el.get_text(' ', strip=True) if meta_el else ''
        author_m = re.search(r'Publiziert von (.+?) am ', meta)
        author = author_m.group(1).strip() if author_m else ''

        content_el = msg.select_one('.o_content')
        body = content_el.get_text('\n', strip=True) if content_el else ''

        if not title:
            continue

        entries.append({
            'date': date_str,
            'title': title,
            'author': author,
            'body': body,
        })
    return entries


def get_mitteilungen(page, course: dict, mitt_url: str | None = None) -> list[dict]:
    """Scrape mitteilungen for a course.

    If mitt_url is provided (from state), skip the landing page navigation.
    """
    if mitt_url is None:
        page.goto(course['url'], wait_until='networkidle')
        mitt_url = _find_mitteilungen_url(page)
    if not mitt_url:
        return []
    page.goto(mitt_url, wait_until='networkidle')
    return _parse_entries(BeautifulSoup(page.content(), 'html.parser'))


def scrape_all(page, courses: list[dict]) -> None:
    state = _load_state()
    changed_ids = _load_changed()

    for course in courses:
        cid = course['id']
        out_dir = os.path.join(DATA_DIR, 'courses', cid)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, 'mitteilungen.json')

        if changed_ids is not None and cid not in changed_ids and os.path.exists(out_file):
            print(f'  [skip] {course["name"][:50]}')
            continue

        mitt_url: str | None = state.get(cid, {}).get('mitt_url')

        try:
            entries = get_mitteilungen(page, course, mitt_url=mitt_url)
        except Exception as e:
            print(f'  ERROR {course["name"][:50]}: {e}')
            entries = []

        with open(out_file, 'w') as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

        label = f'{len(entries)} entries' if entries else 'no Mitteilungen'
        print(f'  [{cid}] {course["name"][:50]} → {label}')


def main():
    courses_file = os.path.join(DATA_DIR, 'courses.json')
    with open(courses_file) as f:
        courses = json.load(f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = make_context(browser)
        page = ctx.new_page()
        ensure_logged_in(ctx, page)
        scrape_all(page, courses)
        browser.close()

    print('\nDone.')


if __name__ == '__main__':
    main()
