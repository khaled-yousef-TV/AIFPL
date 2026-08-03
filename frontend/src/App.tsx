import { useEffect, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'

import type { GameWeekInfo } from './types'
import { ThisWeekTab } from './tabs'
import { HERMES_RUN_TYPES, useHermes } from './hooks/useHermes'
import { useTasks } from './hooks/useTasks'
import type { HermesRunType } from './api/hermes'

// In production set this to your hosted backend, e.g. https://api.fplai.nl
// In local dev it defaults to http://localhost:8001
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8001'

const RUN_TYPE_IDS = HERMES_RUN_TYPES.map((rt) => rt.value as string)
const DEFAULT_VIEW: HermesRunType = 'briefing'

/** Map a URL hash to a run-type view. */
function parseHash(hash: string): HermesRunType {
  return RUN_TYPE_IDS.includes(hash) ? (hash as HermesRunType) : DEFAULT_VIEW
}

const pad = (n: number) => String(n).padStart(2, '0')

function App() {
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [gameweekError, setGameweekError] = useState<string | null>(null)
  // One string, updated once a second — the old header rendered eight glowing
  // gradient tiles and repainted all of them on every tick.
  const [countdown, setCountdown] = useState<string | null>(null)
  const [view, setView] = useState<HermesRunType>(() => parseHash(window.location.hash.slice(1)))

  const hermes = useHermes()
  const { durationStats, anyActive: anyTaskActive } = useTasks()

  const loadGameweek = async () => {
    try {
      const gwRes = await fetch(`${API_BASE}/api/gameweek`).then((r) => r.json())
      setGameweek(gwRes)
      setGameweekError(null)
    } catch (err: any) {
      setGameweekError(err.message || 'Failed to load gameweek')
    }
  }

  useEffect(() => {
    loadGameweek()
  }, [])

  // Sync URL hash when the view changes
  useEffect(() => {
    const expectedHash = view === DEFAULT_VIEW ? '' : view
    const currentHash = window.location.hash.slice(1)
    if (currentHash !== expectedHash) {
      const newUrl = expectedHash === '' ? window.location.pathname : `#${expectedHash}`
      window.history.pushState(null, '', newUrl)
    }
  }, [view])

  // Listen for browser back/forward navigation
  useEffect(() => {
    const handleNavigation = () => setView(parseHash(window.location.hash.slice(1)))
    window.addEventListener('popstate', handleNavigation)
    window.addEventListener('hashchange', handleNavigation)
    return () => {
      window.removeEventListener('popstate', handleNavigation)
      window.removeEventListener('hashchange', handleNavigation)
    }
  }, [])

  // Countdown to the gameweek deadline
  useEffect(() => {
    if (!gameweek?.next?.deadline) {
      setCountdown(null)
      return
    }

    const deadline = new Date(gameweek.next.deadline).getTime()
    const tick = () => {
      const diff = deadline - Date.now()
      if (diff <= 0) {
        setCountdown('00:00:00')
        return
      }
      const days = Math.floor(diff / 86_400_000)
      const hours = Math.floor((diff % 86_400_000) / 3_600_000)
      const minutes = Math.floor((diff % 3_600_000) / 60_000)
      const seconds = Math.floor((diff % 60_000) / 1000)
      setCountdown(`${days > 0 ? `${days}D ` : ''}${pad(hours)}:${pad(minutes)}:${pad(seconds)}`)
    }

    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [gameweek?.next?.deadline])

  const refresh = async () => {
    await Promise.all([loadGameweek(), hermes.refreshLatest()])
  }

  const anyWorking = hermes.anyRunning || anyTaskActive

  // Off-season (gameweek loaded, no next deadline) must not look like loading
  const offSeason = !!gameweek && !gameweek.next

  const deadlineLabel = gameweek?.next
    ? `GW${gameweek.next.id}${countdown ? ` · T−${countdown}` : ''}`
    : offSeason
    ? 'Season finished'
    : gameweekError
    ? 'Gameweek unavailable'
    : 'Loading…'

  return (
    <div className="min-h-screen bg-bg text-content flex flex-col">
      <header className="masthead">
        <span>Hermes FPL</span>
        <span className="hidden sm:inline">Matchday Briefing</span>
        {anyWorking && (
          <span className="flex items-center gap-1.5" title="Hermes is working in the background">
            <Loader2 className="w-3 h-3 animate-spin" aria-hidden />
            <span className="hidden sm:inline">working</span>
          </span>
        )}
        <span className="masthead-clock tabular">{deadlineLabel}</span>
        <button onClick={refresh} className="masthead-btn" aria-label="Refresh">
          <RefreshCw className="w-3 h-3" aria-hidden />
          <span className="hidden sm:inline">Refresh</span>
        </button>
      </header>

      <main className="flex-1">
        <ThisWeekTab
          view={view}
          onViewChange={setView}
          runs={hermes.runs}
          activeByType={hermes.activeByType}
          errors={hermes.errors}
          status={hermes.status}
          avgDurationMs={durationStats['hermes_run']?.avg_duration_ms ?? null}
          onStart={(rt, force, fplTeamId) => hermes.startRun(rt, force, fplTeamId)}
        />
      </main>

      <footer className="border-t border-border mt-10 py-5 px-4 sm:px-6">
        <p className="text-center text-content-subtle text-[0.65rem] font-bold uppercase tracking-[0.18em]">
          Hermes FPL · AI-powered predictions · Not affiliated with the Premier League
        </p>
      </footer>
    </div>
  )
}

export default App
