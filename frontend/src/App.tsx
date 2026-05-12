import { useEffect, useState, useCallback } from 'react'

// ── Types ────────────────────────────────────────────────────────────────────

interface Deadline {
  date: string
  description: string
}

interface CourseSummary {
  course_id: string
  course_code: string | null
  course_type: string | null
  course_title: string
  course_name: string
  ongoing_summary: string
  deadlines: Deadline[]
  requirements: string
  last_activity: string
}

interface DiffFile {
  change: 'added' | 'changed' | 'removed'
  course: string
  filename: string
  type: string
  size: string
  folder_label: string
  priority: boolean
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

function formatTs(iso: string): string {
  const dt = new Date(iso)
  const today = new Date()
  const sameDay = dt.toDateString() === today.toDateString()
  return sameDay
    ? dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : dt.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ── Sub-components ────────────────────────────────────────────────────────────

const TYPE_BADGE: Record<string, string> = {
  VO: 'badge-vo',
  PS: 'badge-ps',
  SE: 'badge-se',
  UE: 'badge-ue',
  PR: 'badge-pr',
}

interface UpcomingDeadline extends Deadline {
  course_id: string
  course_type: string | null
  course_title: string
}

function UpcomingGrid({ courses }: { courses: CourseSummary[] }) {
  const today = new Date().toISOString().slice(0, 10)
  const upcoming: UpcomingDeadline[] = courses
    .flatMap(c => c.deadlines.map(d => ({
      ...d,
      course_id: c.course_id,
      course_type: c.course_type,
      course_title: c.course_title,
    })))
    .filter(d => d.date >= today)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 6)

  if (!upcoming.length) return null

  function scrollToCourse(course_id: string) {
    const el = document.getElementById(`course-${course_id}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.style.transition = 'box-shadow 0s'
    el.style.boxShadow = '0 0 0 2px #fbbf24'
    setTimeout(() => {
      el.style.transition = 'box-shadow 0.7s ease'
      el.style.boxShadow = '0 0 0 2px transparent'
      setTimeout(() => { el.style.boxShadow = ''; el.style.transition = '' }, 800)
    }, 700)
  }

  return (
    <section className="mb-7">
      <h2 className="text-xs font-semibold uppercase tracking-widest text-text-muted mb-2">
        Upcoming Deadlines
      </h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {upcoming.map((d, i) => {
          const badgeClass = d.course_type ? (TYPE_BADGE[d.course_type] ?? 'badge-default') : 'badge-default'
          return (
            <div
              key={i}
              onClick={() => scrollToCourse(d.course_id)}
              className="rounded-lg border border-border bg-card px-3 py-2.5 flex flex-col gap-1 cursor-pointer hover:border-border hover:shadow-sm transition-shadow"
            >
              <div className="flex items-center gap-1.5">
                {d.course_type && (
                  <span className={`text-xs font-bold px-1.5 py-0.5 rounded shrink-0 ${badgeClass}`}>
                    {d.course_type}
                  </span>
                )}
                <span className="text-xs text-text-muted truncate">{d.course_title}</span>
              </div>
              <span className="text-sm font-mono font-semibold text-text-2">{d.date}</span>
              <p className="text-xs text-text-3 leading-snug line-clamp-2">{d.description}</p>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function CourseCard({ course }: { course: CourseSummary }) {
  const [open, setOpen] = useState(false)
  const badgeClass = course.course_type ? (TYPE_BADGE[course.course_type] ?? 'badge-default') : 'badge-default'
  const hasDeadlines = course.deadlines.length > 0

  return (
    <div id={`course-${course.course_id}`} className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Header */}
      <div
        className="px-4 py-3 flex items-center gap-2 cursor-pointer hover:bg-card-hover"
        onClick={() => setOpen(o => !o)}
      >
        {course.course_type && (
          <span className={`text-xs font-bold px-2 py-0.5 rounded shrink-0 ${badgeClass}`}>
            {course.course_type}
          </span>
        )}
        <span className="font-semibold text-sm truncate flex-1 text-text-1">{course.course_title}</span>
        {course.course_code && (
          <span className="text-xs text-text-muted shrink-0">{course.course_code}</span>
        )}
        {hasDeadlines && (
          <span className="text-xs font-semibold text-amber-600 shrink-0">
            {course.deadlines.length} deadline{course.deadlines.length > 1 ? 's' : ''}
          </span>
        )}
        <svg
          className={`w-4 h-4 text-text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Ongoing summary — always visible if non-empty */}
      {course.ongoing_summary && (
        <div className="px-4 pb-3 text-sm text-text-3 border-t border-border-sub pt-2">
          {course.ongoing_summary}
        </div>
      )}

      {/* Requirements — collapsible */}
      {open && course.requirements && (
        <div className="px-4 pb-3 text-sm text-text-3 border-t border-border-sub pt-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-text-muted block mb-1">Prüfungsmodalitäten</span>
          {course.requirements}
        </div>
      )}

      {/* Deadlines — always visible */}
      {hasDeadlines && (
        <div className="border-t border-border-sub">
          {course.deadlines.map((d, i) => (
            <div key={i} className="px-4 py-2 flex items-baseline gap-3 border-b border-border-sub last:border-b-0">
              <span className="text-xs font-mono font-semibold text-text-muted shrink-0">{d.date}</span>
              <span className="text-xs text-text-2">{d.description}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const CHANGE_BADGE: Record<string, string> = {
  added: 'badge-added',
  changed: 'badge-changed',
  removed: 'badge-removed',
}

function DiffSection({ files }: { files: DiffFile[] }) {
  if (!files.length) return (
    <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-text-muted">
      No changes since last run
    </div>
  )
  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {files.map((f, i) => (
        <div key={i} className="px-4 py-2.5 border-b border-border-sub last:border-b-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded ${CHANGE_BADGE[f.change] ?? ''}`}>
              {f.change}
            </span>
            {f.priority && (
              <span className="text-xs font-semibold px-2 py-0.5 rounded badge-priority">
                priority
              </span>
            )}
            <span className="text-xs text-text-muted font-medium">{f.course}</span>
            <span className="ml-auto text-xs text-text-muted">{f.size}</span>
          </div>
          <div className="font-medium text-sm mt-0.5 text-text-1">{f.filename}</div>
          {f.folder_label && <div className="text-xs text-text-muted">{f.folder_label}</div>}
        </div>
      ))}
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [courses, setCourses] = useState<CourseSummary[]>([])
  const [diff, setDiff] = useState<DiffFile[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshStep, setRefreshStep] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [c, d] = await Promise.all([
        apiFetch<CourseSummary[]>('/api/courses/summary'),
        apiFetch<DiffFile[]>('/api/materials/diff'),
      ])
      setCourses(c)
      setDiff(d)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
    try {
      const run = await apiFetch<{ timestamp: string | null }>('/api/last-run')
      if (run.timestamp) setUpdatedAt(formatTs(run.timestamp))
    } catch { /* keep existing timestamp on fetch failure */ }
  }, [])

  useEffect(() => { load() }, [load])

  async function triggerRefresh() {
    setRefreshing(true)
    setRefreshStep(null)
    try {
      await fetch('/api/refresh', { method: 'POST' })
      const poll = async () => {
        try {
          const st = await apiFetch<{ running: boolean; step: string | null; step_n: number; step_total: number; item_n: number | null; item_total: number | null; last: string | null; error: string | null }>('/api/refresh/status')
          if (st.running) {
            if (st.step) {
              const itemPart = st.item_n != null && st.item_total != null
                ? ` ${st.item_n}/${st.item_total}`
                : ` (${st.step_n}/${st.step_total})`
              setRefreshStep(st.step + itemPart)
            } else {
              setRefreshStep(null)
            }
            setTimeout(poll, 2000)
          } else {
            setRefreshing(false)
            if (st.error) {
              const m = st.error.match(/Please try again in ([\d]+m[\d.]+s|[\d.]+s)/i)
              if (m) {
                const t = m[1].replace(/(\d+)\.(\d+)s/, (_, s) => `${s}s`)
                setRefreshStep(`Groq limit — retry in ${t}`)
              } else {
                setRefreshStep(null)
                setError(`Refresh failed: ${st.error}`)
              }
            } else {
              setRefreshStep(null)
            }
            if (!st.error && st.last) setUpdatedAt(formatTs(st.last))
            await load()
          }
        } catch {
          setTimeout(poll, 2000)
        }
      }
      setTimeout(poll, 1000)
    } catch (e) {
      setRefreshing(false)
      setRefreshStep(null)
      setError(String(e))
    }
  }

  const totalDeadlines = courses.reduce((n, c) => n + c.deadlines.length, 0)

  return (
    <div className="min-h-screen bg-page">
      <div className="max-w-2xl mx-auto px-4 py-6 pb-16">

        {/* Header */}
        <header className="flex items-start justify-between mb-6">
          <div className="shrink-0">
            <h1 className="text-xl font-bold tracking-tight text-text-1">
              LMS Dashboard
              {totalDeadlines > 0 && (
                <span className="ml-2 text-sm font-semibold text-amber-600 whitespace-nowrap">
                  {totalDeadlines} deadline{totalDeadlines > 1 ? 's' : ''}
                </span>
              )}
            </h1>
            {updatedAt && (
              <p className="text-xs text-text-muted mt-0.5">Updated {updatedAt}</p>
            )}
          </div>
          <div className="flex flex-col items-end gap-1">
            <button
              onClick={triggerRefresh}
              disabled={refreshing}
              className="px-3 py-1.5 text-sm font-medium border border-border rounded-lg bg-card hover:bg-card-hover disabled:opacity-50 disabled:cursor-not-allowed text-text-2"
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
            {refreshStep && (
              <span className="text-xs text-text-muted text-right">{refreshStep}</span>
            )}
          </div>
        </header>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-lg bg-card text-red-700 text-sm">
            {error}
          </div>
        )}

        <UpcomingGrid courses={courses} />

        {/* Courses */}
        <section className="mb-7">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-text-muted mb-2">
            Courses
          </h2>
          {courses.length === 0 && !error ? (
            <div className="rounded-lg border border-border bg-card px-4 py-8 text-center text-sm text-text-muted">
              No course summaries — run scraper first
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {courses.map(c => (
                <CourseCard key={c.course_id} course={c} />
              ))}
            </div>
          )}
        </section>

        {/* Material changes */}
        <section className="mb-7">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-text-muted mb-2">
            Material Changes
          </h2>
          <DiffSection files={diff} />
        </section>

      </div>
    </div>
  )
}
