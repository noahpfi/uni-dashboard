"""Step 8: FastAPI backend — serves scraped LMS data + triggers refresh"""
import asyncio
import json
import sys
import time as _time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA_DIR = Path(__file__).parent.parent / 'data'
SCRAPER_DIR = Path(__file__).parent
FRONTEND_DIST = Path(__file__).parent.parent / 'frontend' / 'dist'

_VIENNA = ZoneInfo('Europe/Vienna')
_SCHED_HOURS = frozenset({5, 7, 9, 11, 13, 15, 17, 19, 21})


async def _scheduler_loop() -> None:
    while True:
        now = datetime.now(_VIENNA)
        future = [h for h in sorted(_SCHED_HOURS) if h > now.hour]
        if future:
            next_dt = now.replace(hour=future[0], minute=0, second=0, microsecond=0)
        else:
            tomorrow = now + timedelta(days=1)
            next_dt = tomorrow.replace(hour=min(_SCHED_HOURS), minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_dt - now).total_seconds())
        if not _refresh_status['running']:
            asyncio.create_task(_run_pipeline())


@asynccontextmanager
async def lifespan(_: FastAPI):
    asyncio.create_task(_scheduler_loop())
    yield


app = FastAPI(title='LMS Dashboard API', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://localhost:4173'],
    allow_methods=['*'],
    allow_headers=['*'],
)

_refresh_lock = asyncio.Lock()
_refresh_status: dict = {'running': False, 'last': None, 'error': None}


def _read_json(rel: str) -> dict | list:
    p = DATA_DIR / rel
    if not p.exists():
        raise HTTPException(404, f'{rel} not found — run scraper first')
    return json.loads(p.read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# Data endpoints
# ---------------------------------------------------------------------------

@app.get('/api/summary')
def get_summary():
    return _read_json('summary.json')


@app.get('/api/courses/summary')
def get_courses_summary():
    p = DATA_DIR / 'courses_summary.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []


@app.get('/api/courses')
def get_courses():
    return _read_json('courses.json')


@app.get('/api/emails')
def get_emails():
    return _read_json('emails.json')


@app.get('/api/mitteilungen')
def get_mitteilungen():
    items = []
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
            items.append({'course_id': cid, 'course_name': course_name, **e})
    items.sort(key=lambda x: x.get('date') or '', reverse=True)
    return items


@app.get('/api/materials')
def get_materials(only_diff: bool = False):
    courses = []
    for path in sorted(DATA_DIR.glob('courses/*/materials.json')):
        mat = json.loads(path.read_text())
        if only_diff:
            diff = mat.get('diff', {})
            if not any(diff.get(k) for k in ('added', 'changed', 'removed')):
                continue
        courses.append(mat)
    return courses


@app.get('/api/notes')
def get_notes():
    result: dict = {}
    for path in sorted(DATA_DIR.glob('courses/*/notes.json')):
        cid = path.parent.name
        result[cid] = json.loads(path.read_text(encoding='utf-8'))
    return result


class _NoteIn(BaseModel):
    text: str


@app.post('/api/notes/{course_id}')
def add_note(course_id: str, body: _NoteIn):
    notes_path = DATA_DIR / 'courses' / course_id / 'notes.json'
    notes = json.loads(notes_path.read_text(encoding='utf-8')) if notes_path.exists() else []
    notes.append({'text': body.text, 'created_at': datetime.now(timezone.utc).isoformat(), 'resolved': False})
    notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False))
    return {'ok': True}


@app.patch('/api/notes/{course_id}/{idx}/resolve')
def resolve_note(course_id: str, idx: int):
    notes_path = DATA_DIR / 'courses' / course_id / 'notes.json'
    if not notes_path.exists():
        raise HTTPException(404, 'no notes')
    notes = json.loads(notes_path.read_text(encoding='utf-8'))
    if idx < 0 or idx >= len(notes):
        raise HTTPException(404, 'index out of range')
    notes[idx]['resolved'] = True
    notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False))
    return {'ok': True}


@app.delete('/api/notes/{course_id}/{idx}')
def delete_note(course_id: str, idx: int):
    notes_path = DATA_DIR / 'courses' / course_id / 'notes.json'
    if not notes_path.exists():
        raise HTTPException(404, 'no notes')
    notes = json.loads(notes_path.read_text(encoding='utf-8'))
    if idx < 0 or idx >= len(notes):
        raise HTTPException(404, 'index out of range')
    notes.pop(idx)
    notes_path.write_text(json.dumps(notes, indent=2, ensure_ascii=False))
    return {'ok': True}


@app.get('/api/last-run')
def last_run():
    p = DATA_DIR / 'courses_summary.json'
    if not p.exists():
        return {'timestamp': None}
    mtime = p.stat().st_mtime
    return {'timestamp': datetime.fromtimestamp(mtime, timezone.utc).isoformat()}


@app.get('/api/materials/diff')
def get_diff():
    """All new/changed/removed files across all courses."""
    result = []
    for path in sorted(DATA_DIR.glob('courses/*/materials.json')):
        mat = json.loads(path.read_text())
        diff = mat.get('diff', {})
        cname = mat.get('course_name', mat['course_id'])
        for kind in ('added', 'changed', 'removed'):
            for f in diff.get(kind, []):
                result.append({'change': kind, 'course': cname, **f})
    return result


# ---------------------------------------------------------------------------
# Refresh pipeline
# ---------------------------------------------------------------------------

PIPELINE_STEPS = [
    ('courses.py',           'Fetching courses'),
    ('changes.py',           'Detecting changes'),
    ('mitteilungen.py',      'Fetching announcements'),
    ('materials.py',         'Fetching materials'),
    ('downloader.py',        'Downloading files'),
    ('emails.py',            'Fetching emails'),
    ('file_summarizer.py',   'Summarizing files'),
    ('course_summarizer.py', 'Summarizing courses'),
    ('summarizer.py',        'Finalizing'),
]

def _course_dir_count() -> int:
    return sum(1 for d in (DATA_DIR / 'courses').iterdir() if d.is_dir())

def _count_written(pattern: str, since: float) -> tuple[int, int]:
    done = sum(1 for f in DATA_DIR.glob(f'courses/*/{pattern}')
               if f.stat().st_mtime >= since)
    return done, _course_dir_count()

STEP_WATCHERS: dict[str, callable] = {
    'mitteilungen.py':      lambda s: _count_written('mitteilungen.json', s),
    'materials.py':         lambda s: _count_written('materials.json', s),
    'file_summarizer.py':   lambda s: _count_written('file_org_cache.json', s),
    'course_summarizer.py': lambda s: _count_written('course_summary.json', s),
}

async def _watch_step_progress(script: str, since: float) -> None:
    counter = STEP_WATCHERS.get(script)
    if not counter:
        return
    while True:
        try:
            n, total = counter(since)
            _refresh_status['item_n'] = n
            _refresh_status['item_total'] = total
        except Exception:
            pass
        await asyncio.sleep(1)

async def _run_pipeline():
    global _refresh_status
    total = len(PIPELINE_STEPS)
    _refresh_status = {
        'running': True, 'step': None, 'step_n': 0, 'step_total': total,
        'item_n': None, 'item_total': None, 'last': None, 'error': None,
    }
    try:
        for i, (script, label) in enumerate(PIPELINE_STEPS, 1):
            since = _time.time()
            _refresh_status.update({'step': label, 'step_n': i, 'item_n': None, 'item_total': None})
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(SCRAPER_DIR / script),
                cwd=str(SCRAPER_DIR),
            )
            watcher = asyncio.create_task(_watch_step_progress(script, since))
            try:
                await proc.wait()
            finally:
                watcher.cancel()
            if proc.returncode != 0:
                raise RuntimeError(f'{script} exited {proc.returncode}')
        _refresh_status = {
            'running': False, 'step': None, 'step_n': total, 'step_total': total,
            'item_n': None, 'item_total': None,
            'last': datetime.now(timezone.utc).isoformat(), 'error': None,
        }
    except Exception as e:
        _refresh_status = {
            'running': False, 'step': None, 'step_n': 0, 'step_total': total,
            'item_n': None, 'item_total': None,
            'last': datetime.now(timezone.utc).isoformat(), 'error': str(e),
        }


@app.post('/api/refresh')
async def trigger_refresh():
    if _refresh_status['running']:
        return {'status': 'already_running'}
    async with _refresh_lock:
        if not _refresh_status['running']:
            asyncio.create_task(_run_pipeline())
    return {'status': 'started'}


@app.get('/api/refresh/status')
def refresh_status():
    return _refresh_status


# ---------------------------------------------------------------------------
# Serve built frontend (production)
# ---------------------------------------------------------------------------

if FRONTEND_DIST.exists():
    app.mount('/', StaticFiles(directory=str(FRONTEND_DIST), html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('api:app', host='0.0.0.0', port=8000, reload=False)
