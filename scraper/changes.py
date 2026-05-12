"""Step 2: deep per-course change detection → data/state.json + data/changed_courses.json

Walks the full course tree (struct nodes + all briefcase folders recursively + mitteilungen)
so no change is missed. Stores discovered mitt_url and folder list in state so downstream
steps can navigate directly without re-doing discovery.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from auth import make_context, ensure_logged_in, BASE

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
STATE_FILE = os.path.join(DATA_DIR, 'state.json')
CHANGED_FILE = os.path.join(DATA_DIR, 'changed_courses.json')

FOLDER_ICONS = {'o_bc_icon', 'o_folder_icon'}
STRUCT_ICONS = {'o_st_icon'}

_TS_RE = re.compile(r'\b\d{10,13}\b')
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


# ---------------------------------------------------------------------------
# Nav tree helpers (shared with materials.py logic)
# ---------------------------------------------------------------------------

def _node_icon(li_el) -> str:
    link = li_el.select_one('span.o_tree_link a')
    if link:
        for i in link.select('i[class]'):
            for cls in i.get('class', []):
                if cls not in ('o_icon', 'o_icon-fw'):
                    return cls
    return ''


def _parse_tree_nodes(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    nodes = []
    for li in soup.select('.o_tree li[data-nodeid]'):
        nid = li['data-nodeid']
        icon = _node_icon(li)
        label_el = li.select_one('span.o_tree_item')
        label = label_el.get_text(strip=True) if label_el else ''
        nodes.append({'nid': nid, 'icon': icon, 'label': label})
    return nodes


# ---------------------------------------------------------------------------
# Tree discovery
# ---------------------------------------------------------------------------

def _discover_tree(page, cid: str, course_url: str) -> tuple[str | None, list[dict]]:
    """Navigate course landing page; return (mitt_url, folder nodes via BFS)."""
    page.goto(course_url, wait_until='networkidle')

    mitt_url: str | None = None
    link = page.query_selector('a.o_peekview_infomsg_link')
    if link:
        mitt_url = link.get_attribute('href')

    visited_structs: set[str] = set()
    known_folders: dict[str, dict] = {}
    queue: list[dict] = []

    def collect(nodes: list[dict]) -> None:
        for n in nodes:
            if n['icon'] in FOLDER_ICONS:
                known_folders.setdefault(n['nid'], n)
            elif n['icon'] in STRUCT_ICONS and n['nid'] not in visited_structs:
                queue.append(n)

    collect(_parse_tree_nodes(page.content()))

    while queue:
        struct = queue.pop(0)
        if struct['nid'] in visited_structs:
            continue
        visited_structs.add(struct['nid'])
        page.goto(
            f"{BASE}/url/RepositoryEntry/{cid}/CourseNode/{struct['nid']}",
            wait_until='networkidle',
        )
        collect(_parse_tree_nodes(page.content()))

    return mitt_url, list(known_folders.values())


# ---------------------------------------------------------------------------
# Recursive file listing (for hashing)
# ---------------------------------------------------------------------------

def _list_folder(page, cid: str, nid: str, enc_path: str = '', depth: int = 0) -> list[tuple]:
    """Recursively enumerate (nid, path, filename, size) in a briefcase folder."""
    if depth > 5:
        return []

    url = (
        f"{BASE}/auth/RepositoryEntry/{cid}/CourseNode/{nid}/path%3D~~{enc_path}/0"
        if enc_path
        else f"{BASE}/url/RepositoryEntry/{cid}/CourseNode/{nid}"
    )
    page.goto(url, wait_until='domcontentloaded', timeout=60000)
    soup = BeautifulSoup(page.content(), 'html.parser')
    entries: list[tuple] = []

    for row in soup.select('tr.o_folder_row'):
        upload_folder = row.get('data-upload-folder', '')
        drag_file = row.get('data-drag-file', '')

        if upload_folder:
            sub_enc = f"{enc_path}%2F{upload_folder}" if enc_path else upload_folder
            entries.extend(_list_folder(page, cid, nid, sub_enc, depth + 1))
            continue

        if not drag_file:
            continue

        cells = row.select('td')
        size = cells[4].get_text(strip=True) if len(cells) > 4 else ''
        path = enc_path.replace('%2F', '/') if enc_path else ''
        entries.append((nid, path, drag_file, size))

    return entries


def _list_mitt(page, mitt_url: str) -> list[tuple]:
    """Return (date, title) pairs from the mitteilungen page."""
    page.goto(mitt_url, wait_until='networkidle')
    soup = BeautifulSoup(page.content(), 'html.parser')
    entries: list[tuple] = []
    for msg in soup.select('.o_msg.o_block_large'):
        title_el = msg.select_one('h3.o_title')
        title = title_el.get_text(strip=True) if title_el else ''
        if not title:
            continue
        dc = msg.select_one('.o_datecomp')
        date_str = ''
        if dc:
            try:
                y = dc.select_one('[class*=o_year] span').get_text(strip=True)
                mo = dc.select_one('[class*=o_month] span').get_text(strip=True)
                d = dc.select_one('[class*=o_day] span').get_text(strip=True)
                date_str = f'{y}-{mo}-{d}'
            except Exception:
                pass
        entries.append((date_str, title))
    return entries


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def check_courses(page, courses: list[dict]) -> list[dict]:
    state = load_state()
    changed: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for course in courses:
        cid = course['id']
        name = course['name']

        try:
            mitt_url, folders = _discover_tree(page, cid, course['url'])
        except Exception as e:
            print(f'  [ERROR] {name[:50]}: discover tree: {e}')
            changed.append(course)
            continue

        all_files: list[tuple] = []
        for folder in folders:
            try:
                all_files.extend(_list_folder(page, cid, folder['nid']))
            except Exception as e:
                print(f'  [ERROR] {name[:50]}: list folder {folder["label"]!r}: {e}')

        mitt_entries: list[tuple] = []
        if mitt_url:
            try:
                mitt_entries = _list_mitt(page, mitt_url)
            except Exception as e:
                print(f'  [ERROR] {name[:50]}: list mitt: {e}')

        # Include folder structure itself so added/removed empty folders are detected
        folder_sig = sorted(f'{f["nid"]}:{f["label"]}' for f in folders)
        parts = folder_sig + sorted(str(x) for x in all_files) + sorted(str(x) for x in mitt_entries)
        h = _stable_hash('\n'.join(parts))

        prev = state.get(cid, {})
        if prev.get('hash') is None:
            status = 'new'
        elif prev['hash'] != h:
            status = 'changed'
        else:
            status = 'unchanged'

        state[cid] = {
            'hash': h,
            'last_run': now,
            'name': name,
            'mitt_url': mitt_url,
            'folders': folders,
        }
        print(f'  [{status:9s}] {name[:60]}')

        if status != 'unchanged':
            changed.append(course)

    save_state(state)
    with open(CHANGED_FILE, 'w') as f:
        json.dump([c['id'] for c in changed], f)

    return changed


def main() -> None:
    courses_file = os.path.join(DATA_DIR, 'courses.json')
    with open(courses_file) as f:
        courses = json.load(f)

    print(f'Deep-scanning {len(courses)} courses...')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = make_context(browser)
        page = ctx.new_page()
        ensure_logged_in(ctx, page)
        changed = check_courses(page, courses)
        browser.close()

    print(f'\n{len(changed)} changed, {len(courses) - len(changed)} unchanged')


if __name__ == '__main__':
    main()
