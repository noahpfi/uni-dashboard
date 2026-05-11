"""Step 4: per-course material discovery → data/courses/<id>/materials.json"""
import json
import os
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from auth import make_context, ensure_logged_in, BASE

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

FOLDER_ICONS = {'o_bc_icon', 'o_folder_icon'}
STRUCT_ICONS = {'o_st_icon'}

SKIP_TYPES = {'mp4', 'mov', 'avi', 'mkv', 'zip', 'rar', '7z', 'tar', 'gz'}

SKIP_LABEL_RE = re.compile(
    r'(altklausur|archiv|literatur|bücher|videos?(\s+der)?|aufzeichnung)',
    re.IGNORECASE,
)
PRIORITY_LABEL_RE = re.compile(
    r'(info|organis|syllabus|termin|abgabe|aufgabe|übung|blatt|klausur|prüfung'
    r'|folien|unterlagen|slides?|material)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Nav tree parsing
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


def _discover_folders(page, cid: str) -> list[dict]:
    """BFS through struct nodes to find all Briefcase (o_bc_icon) folders."""
    visited_structs: set[str] = set()
    known_folders: dict[str, dict] = {}
    queue: list[dict] = []

    def collect(nodes):
        for n in nodes:
            if n['icon'] in FOLDER_ICONS:
                if n['nid'] not in known_folders:
                    known_folders[n['nid']] = n
            elif n['icon'] in STRUCT_ICONS:
                if n['nid'] not in visited_structs:
                    queue.append(n)

    collect(_parse_tree_nodes(page.content()))

    while queue:
        struct = queue.pop(0)
        if struct['nid'] in visited_structs:
            continue
        visited_structs.add(struct['nid'])
        page.goto(f"{BASE}/url/RepositoryEntry/{cid}/CourseNode/{struct['nid']}",
                  wait_until='networkidle')
        collect(_parse_tree_nodes(page.content()))

    return list(known_folders.values())


# ---------------------------------------------------------------------------
# Briefcase file scraping (with subfolder recursion)
# ---------------------------------------------------------------------------

def _scrape_bc(page, cid: str, nid: str, enc_path: str = '',
               depth: int = 0, node_label: str = '') -> list[dict]:
    """Recursively scrape files from a Briefcase node.

    enc_path: URL-encoded path within the node (empty = root of node).
    """
    if depth > 5:
        return []

    if enc_path:
        url = f"{BASE}/auth/RepositoryEntry/{cid}/CourseNode/{nid}/path%3D~~{enc_path}/0"
    else:
        url = f"{BASE}/url/RepositoryEntry/{cid}/CourseNode/{nid}"

    page.goto(url, wait_until='networkidle')
    soup = BeautifulSoup(page.content(), 'html.parser')
    files = []

    for row in soup.select('tr.o_folder_row'):
        upload_folder = row.get('data-upload-folder', '')
        drag_file = row.get('data-drag-file', '')

        if upload_folder:
            # Subfolder — recurse
            sub_enc = (f"{enc_path}%2F{upload_folder}" if enc_path
                       else upload_folder)
            files.extend(_scrape_bc(page, cid, nid, sub_enc,
                                    depth + 1, upload_folder))
            continue

        if not drag_file:
            continue

        cells = row.select('td')
        if len(cells) < 4:
            continue

        ftype = cells[3].get_text(strip=True).lower()
        if ftype in SKIP_TYPES:
            continue

        title = cells[1].get_text(' ', strip=True)
        creator = cells[2].get_text(strip=True) if len(cells) > 2 else ''
        size = cells[4].get_text(strip=True) if len(cells) > 4 else ''
        folder_path = enc_path.replace('%2F', '/') if enc_path else ''

        priority = bool(
            PRIORITY_LABEL_RE.search(node_label) or
            PRIORITY_LABEL_RE.search(folder_path) or
            PRIORITY_LABEL_RE.search(title)
        )

        files.append({
            'filename': drag_file,
            'title': title,
            'creator': creator,
            'type': ftype,
            'size': size,
            'folder_nid': nid,
            'folder_label': node_label,
            'folder_path': folder_path,
            'priority': priority,
        })

    return files


# ---------------------------------------------------------------------------
# Per-course orchestration
# ---------------------------------------------------------------------------

def _scrape_course(page, course: dict) -> list[dict]:
    cid = course['id']
    page.goto(course['url'], wait_until='networkidle')
    folders = _discover_folders(page, cid)

    all_files = []
    for folder in folders:
        label = folder['label']
        if SKIP_LABEL_RE.search(label):
            print(f"    [skip] {label}")
            continue
        files = _scrape_bc(page, cid, folder['nid'], node_label=label)
        print(f"    [{label}] {len(files)} file(s)")
        all_files.extend(files)

    return all_files


def _file_key(f: dict) -> str:
    return f"{f.get('folder_nid','')}/{f.get('folder_path','')}/{f['filename']}"


def _diff(prev: list[dict], curr: list[dict]) -> dict:
    prev_map = {_file_key(f): f for f in prev}
    curr_map = {_file_key(f): f for f in curr}
    return {
        'added': [f for k, f in curr_map.items() if k not in prev_map],
        'removed': [prev_map[k] for k in prev_map if k not in curr_map],
        'changed': [f for k, f in curr_map.items()
                    if k in prev_map and prev_map[k].get('size') != f.get('size')],
    }


def scrape_all(page, courses: list[dict]) -> None:
    for course in courses:
        cid = course['id']
        out_dir = os.path.join(DATA_DIR, 'courses', cid)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, 'materials.json')

        prev_files: list[dict] = []
        if os.path.exists(out_file):
            with open(out_file) as f:
                prev_files = json.load(f).get('files', [])

        try:
            files = _scrape_course(page, course)
        except Exception as e:
            print(f"  ERROR {course['name'][:50]}: {e}")
            continue

        diff = _diff(prev_files, files)
        result = {
            'course_id': cid,
            'course_name': course['name'],
            'files': files,
            'diff': diff,
        }
        with open(out_file, 'w') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        n_files = len(files)
        n_added = len(diff['added'])
        label = course['name'][:50]
        summary = f"{n_files} file(s)"
        if n_added:
            summary += f", +{n_added} new: {[x['filename'] for x in diff['added'][:3]]}"
        print(f"  [{cid}] {label}: {summary}")


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
