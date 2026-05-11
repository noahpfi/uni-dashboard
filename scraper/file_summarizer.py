"""Extract organizational info from downloaded file texts → per-course cache"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

from groq_client import GROQ_MODEL, groq_chat, make_clients  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / 'data'
TEXT_CAP  = 20_000   # chars read from each file sent to extract call
BATCH_CAP = 15_000   # max chars per extract API call

SELECT_PROMPT = """\
You are given a list of NON-PRIORITY files from a university course.
Select files that might contain ANY of the following:
- Specific dates (exam, submission deadline, registration deadline)
- Deadlines for homework, projects, or assignments
- A course schedule or Terminplan
- Room or time changes, office hours

Exclude ONLY obvious negatives: past exam papers (e.g. "exam_ws23", "Prüfung_2024"),
solution/Lösung/Musterlösung sheets, or clearly pure math exercise sheets with no dates.
When in doubt, include the file.

Return ONLY the label strings of relevant files as JSON:
{"relevant": ["label1", "label2"]}
If none are relevant return {"relevant": []}.
"""

EXTRACT_PROMPT = """\
You receive text extracted from one or more university course documents.
Each document is preceded by a === label === header.
Extract ONLY organizational information: deadlines, exam dates, submission deadlines, room changes,
mandatory attendance requirements, registration deadlines, office hours changes.
Include dates even if context is partial — return exactly the text as found in the document.
If no organizational information found, return {"items": []}.

Output valid JSON only:
{"items": [{"date": "YYYY-MM-DD or null", "text": "<exact text from document>"}]}
"""


def _load_cache(cid: str) -> dict:
    p = DATA_DIR / 'courses' / cid / 'file_org_cache.json'
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def _save_cache(cid: str, cache: dict) -> None:
    p = DATA_DIR / 'courses' / cid / 'file_org_cache.json'
    p.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding='utf-8')


def _label(f: dict) -> str:
    folder = f.get('folder_label', '').strip()
    name = f.get('filename', '')
    return f'{folder} / {name}' if folder else name


def process_course(cid: str, course_name: str, clients: list) -> None:
    mat_path = DATA_DIR / 'courses' / cid / 'materials.json'
    if not mat_path.exists():
        return

    mat = json.loads(mat_path.read_text(encoding='utf-8'))
    cache = _load_cache(cid)

    # Separate priority vs non-priority uncached files
    priority_pending = []
    nonprio_pending = []
    for f in mat.get('files', []):
        tf = f.get('text_file')
        if not tf or tf in cache:
            continue
        tpath = DATA_DIR / tf
        if not tpath.exists():
            continue
        entry = (tf, _label(f), tpath)
        if f.get('priority'):
            priority_pending.append(entry)
        else:
            nonprio_pending.append(entry)

    if not priority_pending and not nonprio_pending:
        return

    total = len(priority_pending) + len(nonprio_pending)
    print(f'  [{cid}] {course_name} — {total} uncached ({len(priority_pending)} priority, {len(nonprio_pending)} non-priority)')

    def flush(texts: list[str], keys: list[str]) -> None:
        combined = '\n\n'.join(texts)
        resp = groq_chat(
            clients,
            model=GROQ_MODEL,
            messages=[
                {'role': 'system', 'content': EXTRACT_PROMPT},
                {'role': 'user', 'content': f'Course: {course_name}\n\n{combined}'},
            ],
            response_format={'type': 'json_object'},
            temperature=0.0,
        )
        items = json.loads(resp.choices[0].message.content).get('items', [])
        cache[keys[0]] = items
        for k in keys[1:]:
            cache[k] = []
        print(f'    → {len(items)} item(s) from {len(keys)} file(s)')

    def batch_and_flush(pending: list[tuple]) -> None:
        batch_texts: list[str] = []
        batch_keys: list[str] = []
        batch_chars = 0
        for tf, label, tpath in pending:
            text = tpath.read_text(encoding='utf-8')[:TEXT_CAP]
            snippet = f'=== {label} ===\n{text}'
            if batch_texts and batch_chars + len(snippet) > BATCH_CAP:
                flush(batch_texts, batch_keys)
                batch_texts, batch_keys, batch_chars = [], [], 0
            batch_texts.append(snippet)
            batch_keys.append(tf)
            batch_chars += len(snippet)
        if batch_texts:
            flush(batch_texts, batch_keys)

    # Priority files: always extract, no filter
    if priority_pending:
        batch_and_flush(priority_pending)

    # Non-priority: run SELECT filter first, cache rejects as []
    if nonprio_pending:
        file_list = '\n'.join(f'- {label}' for _, label, _ in nonprio_pending)
        resp = groq_chat(
            clients,
            model=GROQ_MODEL,
            messages=[
                {'role': 'system', 'content': SELECT_PROMPT},
                {'role': 'user', 'content': f'Course: {course_name}\n\n{file_list}'},
            ],
            response_format={'type': 'json_object'},
            temperature=0.0,
        )
        relevant_labels = set(json.loads(resp.choices[0].message.content).get('relevant', []))
        selected_nonprio = []
        for tf, label, tpath in nonprio_pending:
            if label in relevant_labels:
                selected_nonprio.append((tf, label, tpath))
            else:
                cache[tf] = []
        print(f'    non-priority filter: {len(selected_nonprio)}/{len(nonprio_pending)} selected')
        if selected_nonprio:
            batch_and_flush(selected_nonprio)

    _save_cache(cid, cache)


def main():
    clients = make_clients()
    for cid_dir in sorted((DATA_DIR / 'courses').iterdir()):
        if not cid_dir.is_dir():
            continue
        cid = cid_dir.name
        mat_path = cid_dir / 'materials.json'
        if not mat_path.exists():
            continue
        mat = json.loads(mat_path.read_text(encoding='utf-8'))
        process_course(cid, mat.get('course_name', cid), clients)
    print('Done.')


if __name__ == '__main__':
    main()
