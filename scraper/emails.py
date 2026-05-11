"""Step 6: IMAP email reader + dedup vs Mitteilungen → data/emails.json"""
import argparse
import email
import imaplib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

DATA_DIR = Path(__file__).parent.parent / 'data'

IMAP_HOST = os.environ['IMAP_HOST']
IMAP_PORT = int(os.environ.get('IMAP_PORT', 993))
IMAP_USER = os.environ['IMAP_USER']
IMAP_PASSWORD = os.environ['IMAP_PASSWORD']

# Explicit LMS senders (always include regardless of subject)
LMS_SENDER_EXACT = ('lms.uibk', 'openolat')
# Subject keywords — only applied when sender is @uibk.ac.at
LMS_SUBJECT_KW = (
    'mitteilung', 'ankündigung', 'aufgabe', 'prüfung', 'klausur',
    'abgabe', 'frist', 'kurs', 'olat', 'lms',
)
BODY_CAP = 5000


def _decode_str(h: str) -> str:
    parts = decode_header(h or '')
    out = []
    for data, charset in parts:
        if isinstance(data, bytes):
            out.append(data.decode(charset or 'utf-8', errors='replace'))
        else:
            out.append(data)
    return ''.join(out)


def _plain_body(msg) -> str:
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                raw = part.get_payload(decode=True)
                if raw:
                    cs = part.get_content_charset() or 'utf-8'
                    body += raw.decode(cs, errors='replace')
    else:
        raw = msg.get_payload(decode=True)
        if raw:
            cs = msg.get_content_charset() or 'utf-8'
            body = raw.decode(cs, errors='replace')
    return body.strip()


def _is_lms_mail(sender: str, subject: str) -> bool:
    sl = sender.lower()
    su = subject.lower()
    if any(kw in sl for kw in LMS_SENDER_EXACT):
        return True
    if 'uibk.ac.at' in sl and any(kw in su for kw in LMS_SUBJECT_KW):
        return True
    return False


def _load_mitt_titles() -> list[str]:
    titles = []
    for path in sorted(DATA_DIR.glob('courses/*/mitteilungen.json')):
        for e in json.loads(path.read_text()):
            t = (e.get('title') or '').strip()
            if t:
                titles.append(t.lower())
    return titles


def _is_duplicate(subject: str, body: str, mitt_titles: list[str]) -> bool:
    sl = subject.lower()
    bl = body.lower()
    for title in mitt_titles:
        if title in sl or title in bl:
            return True
    return False


def _parse_date(date_str: str) -> str | None:
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc).strftime('%Y-%m-%d')
    except Exception:
        return None


def fetch_emails(days_back: int = 30, folder: str = 'INBOX') -> list[dict]:
    mitt_titles = _load_mitt_titles()
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime('%d-%b-%Y')

    results = []
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
        imap.login(IMAP_USER, IMAP_PASSWORD)

        # Discover folders containing "lms" or "kurs" in name
        folders_to_check = [folder]
        _, raw_list = imap.list()
        for item in raw_list:
            name = item.decode() if isinstance(item, bytes) else item
            m = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', name)
            if m:
                fname = (m.group(1) or m.group(2) or '').strip('"')
                if fname != folder and any(
                    kw in fname.lower() for kw in ('lms', 'kurs', 'uibk', 'olat')
                ):
                    folders_to_check.append(fname)

        seen_ids: set[str] = set()

        for f in folders_to_check:
            try:
                imap.select(f, readonly=True)
            except Exception as e:
                print(f"  [skip folder] {f}: {e}")
                continue

            _, data = imap.search(None, f'SINCE {since}')
            uids = data[0].split()
            print(f"  {f}: {len(uids)} emails in range")

            for uid in uids:
                _, msg_data = imap.fetch(uid, '(RFC822)')
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                msg_id = msg.get('Message-ID', '')
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                sender = _decode_str(msg.get('From', ''))
                subject = _decode_str(msg.get('Subject', ''))

                if not _is_lms_mail(sender, subject):
                    continue

                body = _plain_body(msg)
                date_iso = _parse_date(msg.get('Date', ''))
                duplicate = _is_duplicate(subject, body, mitt_titles)

                results.append({
                    'date': date_iso,
                    'from': sender,
                    'subject': subject,
                    'body': body[:BODY_CAP],
                    'duplicate': duplicate,
                })

    # Newest first
    results.sort(key=lambda e: e['date'] or '', reverse=True)
    return results


def main():
    ap = argparse.ArgumentParser(description='Step 6: fetch LMS emails via IMAP')
    ap.add_argument('--days', type=int, default=30, help='Lookback window in days')
    ap.add_argument('--folder', default='INBOX', help='Primary IMAP folder to search')
    args = ap.parse_args()

    print(f'Fetching emails (last {args.days} days)...')
    emails = fetch_emails(days_back=args.days, folder=args.folder)

    out = DATA_DIR / 'emails.json'
    out.write_text(json.dumps(emails, indent=2, ensure_ascii=False))

    non_dup = sum(1 for e in emails if not e['duplicate'])
    print(f'\n{len(emails)} LMS emails | {non_dup} non-duplicate → {out}')


if __name__ == '__main__':
    main()
