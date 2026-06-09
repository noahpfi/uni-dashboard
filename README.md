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

```bash
cp .env.example .env   # fill in credentials
pip install -r requirements.txt
playwright install chromium
```

Run scraper: `python scraper/run.py`  
Start API: `uvicorn api.main:app`  
Start frontend: `cd frontend && npm install && npm run dev`
