"""Per-course Groq summary → data/courses/<cid>/course_summary.json + data/courses_summary.json"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

from groq_client import GROQ_MODEL, groq_chat, make_clients  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / 'data'

SYSTEM_PROMPT = """\
You are an academic assistant for a university student at UIBK.
You receive data for ONE course: announcements, newly added/changed materials,
organizational extracts from files, and relevant emails.

Output valid JSON only:
{
  "ongoing_summary": "<2-4 sentences about what is currently open or upcoming: open assignments, upcoming exams, room/schedule changes, important announcements. German preferred. Empty string if nothing active.>",
  "deadlines": [
    {"date": "YYYY-MM-DD", "description": "<one clear sentence: what must be done, for which course/exam/assignment, and any relevant detail (e.g. location, submission method, group size). Not just a label — a student should know what to do from this alone.>"}
  ],
  "requirements": "<Prüfungsmodalitäten: how to pass, grading breakdown, attendance rules, minimum score thresholds. 2-4 sentences. German preferred. Empty string if not found in the data.>",
  "last_activity": "YYYY-MM-DD"
}

Rules:
- deadlines: only future or today dates. Sort ascending. Deduplicate.
- last_activity: most recent date any university-side update happened.
- If no deadlines, return [].
"""


def parse_course_name(raw: str) -> dict:
    m = re.match(r'^(\d{4}[SW]\d+)\s+([A-Z]+)\s+(.+)$', raw)
    if m:
        return {'code': m.group(1), 'type': m.group(2), 'title': m.group(3)}
    m = re.match(r'^([A-Z]+)\s+(.+)$', raw)
    if m:
        return {'code': None, 'type': m.group(1), 'title': m.group(2)}
    return {'code': None, 'type': None, 'title': raw}


_SCHEMA_VERSION = 'v2'  # bump when prompt schema changes to invalidate cache

def _payload_hash(text: str) -> str:
    return hashlib.sha256(f'{_SCHEMA_VERSION}:{text}'.encode()).hexdigest()[:16]


def build_course_payload(cid: str, mat: dict) -> str:
    sections = []

    notes_path = DATA_DIR / 'courses' / cid / 'notes.json'
    if notes_path.exists():
        raw_notes = json.loads(notes_path.read_text(encoding='utf-8'))
        active = [n for n in raw_notes if not n.get('resolved')]
        active.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        if active:
            lines = ['## Student Notes (user-provided, high priority)']
            for n in active:
                lines.append(f"[{n['created_at'][:10]}] {n['text']}")
            sections.append('\n'.join(lines))

    mitt_path = DATA_DIR / 'courses' / cid / 'mitteilungen.json'
    if mitt_path.exists():
        items = json.loads(mitt_path.read_text(encoding='utf-8'))
        if items:
            lines = ['## Mitteilungen']
            for m in sorted(items, key=lambda x: x.get('date') or '', reverse=True)[:10]:
                lines.append(
                    f"\n[{m.get('date', '?')}] {m.get('title', '')}"
                    f"\nAuthor: {m.get('author', '')}"
                    f"\n{m.get('body', '')[:1000]}"
                )
            sections.append('\n'.join(lines))

    diff = mat.get('diff', {})
    new_files = diff.get('added', []) + diff.get('changed', [])
    if new_files:
        lines = ['## Neue/Geänderte Materialien']
        for f in new_files:
            prio = ' [priority]' if f.get('priority') else ''
            lines.append(f"- {f['filename']} ({f.get('type', '')}, {f.get('size', '')}){prio}")
        sections.append('\n'.join(lines))

    cache_path = DATA_DIR / 'courses' / cid / 'file_org_cache.json'
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
        org_items = [item for items in cache.values() for item in (items or [])]
        if org_items:
            lines = ['## Organisatorische Infos aus Dateien']
            for item in org_items:
                lines.append(f"[{item.get('date', '')}] {item.get('text', '')}")
            sections.append('\n'.join(lines))

    emails_path = DATA_DIR / 'emails.json'
    if emails_path.exists():
        course_name = mat.get('course_name', cid)
        emails = json.loads(emails_path.read_text(encoding='utf-8'))
        course_emails = [
            e for e in emails
            if not e.get('duplicate')
            and course_name.lower() in (e.get('subject', '') + e.get('body', '')).lower()
        ]
        if course_emails:
            lines = ['## Relevante Emails']
            for e in course_emails[:5]:
                lines.append(
                    f"\n[{e.get('date', '?')}] {e.get('subject', '')}"
                    f"\n{e.get('body', '')[:500]}"
                )
            sections.append('\n'.join(lines))

    return '\n\n'.join(sections)


def summarize_course(cid: str, clients: list, today: str) -> dict | None:
    mat_path = DATA_DIR / 'courses' / cid / 'materials.json'
    if not mat_path.exists():
        return None

    mat = json.loads(mat_path.read_text(encoding='utf-8'))
    course_name_raw = mat.get('course_name', cid)
    payload = build_course_payload(cid, mat)

    if not payload.strip():
        return None

    summary_path = DATA_DIR / 'courses' / cid / 'course_summary.json'
    phash = _payload_hash(payload)
    if summary_path.exists():
        prev = json.loads(summary_path.read_text(encoding='utf-8'))
        if prev.get('_hash') == phash:
            print(f'  [{cid}] unchanged, skip')
            return prev

    print(f'  [{cid}] {course_name_raw} ({len(payload):,} chars)...')

    resp = groq_chat(
        clients,
        model=GROQ_MODEL,
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': f'Today: {today}\nCourse: {course_name_raw}\n\n{payload}'},
        ],
        response_format={'type': 'json_object'},
        temperature=0.1,
    )
    result = json.loads(resp.choices[0].message.content)

    parsed = parse_course_name(course_name_raw)
    out = {
        '_hash': phash,
        'course_id': cid,
        'course_code': parsed['code'],
        'course_type': parsed['type'],
        'course_title': parsed['title'],
        'course_name': course_name_raw,
        'ongoing_summary': result.get('ongoing_summary', ''),
        'deadlines': result.get('deadlines', []),
        'requirements': result.get('requirements', ''),
        'last_activity': result.get('last_activity', ''),
    }

    summary_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def main():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    clients = make_clients()

    results = []
    for cid_dir in sorted((DATA_DIR / 'courses').iterdir()):
        if not cid_dir.is_dir():
            continue
        result = summarize_course(cid_dir.name, clients, today)
        if result:
            results.append(result)

    results.sort(key=lambda x: x.get('last_activity', ''), reverse=True)
    out_path = DATA_DIR / 'courses_summary.json'
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f'\n{len(results)} courses → {out_path}')


if __name__ == '__main__':
    main()
