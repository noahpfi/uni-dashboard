# uni-dashboard

Personal dashboard for University of Innsbruck (UIBK). Scrapes OpenOLAT LMS and university email, runs an LLM filter to extract only actionable items — deadlines, exams, assignments, room/time changes — and surfaces them in a React frontend.

## How it works

A Playwright scraper handles Shibboleth SSO + TOTP login, enumerates enrolled courses, detects changes per-course, and pulls announcements and course materials. A separate IMAP reader pulls university emails. Both feeds are batched through Groq (llama-3.3-70b-versatile), which extracts organizational signal only — no lecture content. Results are served via FastAPI and displayed in a Vite + React frontend.

## Stack

| Layer | Tech |
|---|---|
| Scraper | Python, Playwright, pyotp |
| Text extraction | pdfminer.six, python-pptx, python-docx |
| LLM | Groq API — llama-3.3-70b-versatile |
| Email | imaplib (IMAP SSL) |
| Backend | FastAPI |
| Frontend | Vite + React + TypeScript |
| Hosting | Self-hosted VPS, nginx, cron |

## Setup

Python deps are managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`). Node is only needed to rebuild the frontend — the built `frontend/dist` is committed, so running the app doesn't require it.

```bash
cp .env.example .env              # fill in credentials
uv sync                           # install Python deps from uv.lock
uv run playwright install chromium
```

## Running

```bash
uv run python scraper/api.py      # FastAPI on :8000, also serves the prebuilt frontend/dist
```

Then open http://localhost:8000. Set `DASH_PASS` in `.env` to require HTTP basic auth (empty = open, dev mode).

For frontend development with hot reload, run the Vite dev server (on :5173, expects the backend on :8000):

```bash
cd frontend && npm install && npm run dev
```

`bash start.sh` runs both the backend (via uv) and the Vite dev server together for local development.
