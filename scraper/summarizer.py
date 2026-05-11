"""Step 7: Groq API filtering → data/summary.json"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

from groq_client import GROQ_MODEL, groq_chat, make_clients  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / 'data'
TEXT_CAP = 3000  # chars per extracted file fed to LLM
MITT_DAYS = 14   # only feed mitteilungen this recent


SYSTEM_PROMPT = """\
You are an academic assistant for a university student at UIBK (University of Innsbruck).
You receive raw LMS (OpenOLAT) data: announcements (Mitteilungen), newly uploaded materials,
and course emails. Your job: extract ONLY organizationally relevant items.

Relevant = deadlines, exams, assignment submissions, schedule changes, mandatory info.
Irrelevant = general greetings, "good luck on the exam" wishes, purely informational
  lecture summaries without actionable content, unchanged repetitive admin notices.

Output ONLY valid JSON, no markdown fences, matching this schema exactly:
{
  "items": [
    {
      "type": "deadline|announcement|material|reminder",
      "urgency": "high|medium|low",
      "course": "<short course name>",
      "date": "<YYYY-MM-DD or null>",
      "title": "<concise German title>",
      "summary": "<1-3 sentences in German>",
      "action": "<what student must do, or empty string>"
    }
  ]
}

Urgency rules:
- high: exam/Klausur date, submission deadline, mandatory attendance change
- medium: new required material, schedule change, important announcement
- low: informational, optional content, general reminders

Deduplicate: if two sources say the same thing, emit one item.
Sort by urgency (high first), then by date ascending.
"""


def _build_payload(days: int) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
    sections = []

    # --- P1: Mitteilungen ---
    mitt_items = []
    for path in sorted(DATA_DIR.glob('courses/*/mitteilungen.json')):
        cid = path.parent.name
        mat_path = path.parent / 'materials.json'
        course_name = cid
        if mat_path.exists():
            try:
                course_name = json.loads(mat_path.read_text()).get('course_name', cid)
            except Exception:
                pass
        for e in json.loads(path.read_text()):
            if (e.get('date') or '') >= cutoff:
                mitt_items.append({
                    'course': course_name,
                    'date': e.get('date'),
                    'title': e.get('title', ''),
                    'author': e.get('author', ''),
                    'body': e.get('body', ''),
                })

    mitt_items.sort(key=lambda x: x.get('date') or '', reverse=True)

    if mitt_items:
        lines = ['## Mitteilungen (Announcements)']
        for m in mitt_items:
            lines.append(
                f"\n[{m['date']}] {m['course']}\n"
                f"Title: {m['title']}\n"
                f"Author: {m['author']}\n"
                f"Body:\n{m['body']}"
            )
        sections.append('\n'.join(lines))

    # --- P2: Material diffs (new/changed files) ---
    new_files = []
    for path in sorted(DATA_DIR.glob('courses/*/materials.json')):
        mat = json.loads(path.read_text())
        cname = mat.get('course_name', mat['course_id'])
        for f in mat.get('diff', {}).get('added', []) + mat.get('diff', {}).get('changed', []):
            new_files.append({
                'course': cname,
                'filename': f['filename'],
                'type': f.get('type', ''),
                'size': f.get('size', ''),
                'folder': f.get('folder_label', ''),
                'priority': f.get('priority', False),
                'text_file': f.get('text_file', ''),
            })

    if new_files:
        lines = ['\n## New/Changed Materials']
        for f in new_files:
            prio = ' [priority]' if f['priority'] else ''
            lines.append(
                f"- {f['course']}: {f['filename']} ({f['type']}, {f['size']}, "
                f"folder: {f['folder']}){prio}"
            )
        sections.append('\n'.join(lines))

    # --- P3: Text extracts for new priority files ---
    text_entries = []
    for nf in new_files:
        if nf.get('text_file'):
            tpath = DATA_DIR / nf['text_file']
            if tpath.exists():
                text = tpath.read_text(encoding='utf-8')[:TEXT_CAP]
                text_entries.append(
                    f"\n### {nf['filename']} ({nf['course']})\n{text}"
                )

    if text_entries:
        sections.append('\n## Extracted Text from New Files\n' + '\n'.join(text_entries))

    # --- P4: Non-duplicate emails ---
    emails_path = DATA_DIR / 'emails.json'
    if emails_path.exists():
        emails = [e for e in json.loads(emails_path.read_text())
                  if not e.get('duplicate') and (e.get('date') or '') >= cutoff]
        if emails:
            lines = ['\n## Non-duplicate Emails']
            for e in emails:
                lines.append(
                    f"\n[{e['date']}] From: {e['from']}\n"
                    f"Subject: {e['subject']}\n"
                    f"Body:\n{e['body'][:1500]}"
                )
            sections.append('\n'.join(lines))

    return '\n\n'.join(sections)


def summarize(days: int = MITT_DAYS) -> dict:
    payload = _build_payload(days)

    if not payload.strip():
        print('No data to summarize.')
        return {'items': []}

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    user_msg = f"Today is {today}. Summarize the following LMS data:\n\n{payload}"

    print(f'Sending {len(user_msg):,} chars to {GROQ_MODEL}...')

    clients = make_clients()
    resp = groq_chat(
        clients,
        model=GROQ_MODEL,
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_msg},
        ],
        response_format={'type': 'json_object'},
        temperature=0.1,
    )

    raw = resp.choices[0].message.content
    result = json.loads(raw)

    return {
        'generated_at': today,
        'model': GROQ_MODEL,
        'sources': {
            'mitteilungen_since': (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).strftime('%Y-%m-%d'),
            'emails_non_dup': sum(
                1 for e in json.loads((DATA_DIR / 'emails.json').read_text())
                if not e.get('duplicate')
            ) if (DATA_DIR / 'emails.json').exists() else 0,
        },
        'items': result.get('items', []),
    }


def main():
    ap = argparse.ArgumentParser(description='Step 7: Groq LMS summarizer')
    ap.add_argument('--days', type=int, default=MITT_DAYS,
                    help='Lookback window for mitteilungen (default 14)')
    args = ap.parse_args()

    result = summarize(days=args.days)
    out = DATA_DIR / 'summary.json'
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    n = len(result.get('items', []))
    high = sum(1 for i in result['items'] if i.get('urgency') == 'high')
    print(f'\n{n} items ({high} high-urgency) → {out}')
    for item in result['items']:
        u = item.get('urgency', '?')[0].upper()
        print(f"  [{u}] {item.get('date','?')} | {item.get('course','')[:25]} | {item.get('title','')[:50]}")


if __name__ == '__main__':
    main()
