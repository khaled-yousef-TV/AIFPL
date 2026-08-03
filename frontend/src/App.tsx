import { useEffect, useState } from 'react'
import { ChevronDown, Loader2, RefreshCw } from 'lucide-react'

import type { GameWeekInfo } from './types'
import { ThisWeekTab } from './tabs'
import SeasonView from './tabs/SeasonView'
import HermesAccountability from './tabs/HermesAccountability'
import { useHermes } from './hooks/useHermes'
import { useTasks } from './hooks/useTasks'
import type { HermesRunType } from './api/hermes'
import {
  DEFAULT_ROUTE,
  PRIMARY_TABS,
  SCENARIO_ITEMS,
  hashToRoute,
  routeToHash,
  type Route,
} from './nav'

// In production set this to your hosted backend, e.g. https://api.fplai.nl
// In local dev it defaults to http://localhost:8001
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8001'

const pad = (n: number) => String(n).padStart(2, '0')


/**
 * Four primary tabs + a Scenarios overflow menu. Replaces the old flat
 * 8-item run-type nav — those run types now live under Scenarios except
 * for `tracked` (the Squad tab) and `briefing` (the This Week tab).
 */
const TopNav: React.FC<{
  route: Route
  onRoute: (r: Route) => void
  activeByType: Record<string, unknown>
}> = ({ route, onRoute, activeByType }) => {
  const [openMenu, setOpenMenu] = useState(false)
  const scenarioActive =
    route.top === 'scenario' &&
    SCENARIO_ITEMS.find((s) => s.runType === route.runType)?.label
  const anyScenarioRunning = SCENARIO_ITEMS.some((s) => activeByType[s.runType])

  // Close on outside-click / Escape. The onMouseLeave approach fired before
  // onClick could register on the menu items — clicks got lost.
  useEffect(() => {
    if (!openMenu) return
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement | null
      if (el && !el.closest('[data-scenarios-menu]')) setOpenMenu(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenMenu(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [openMenu])

  return (
    <nav className="run-nav scrollbar-hide" aria-label="Primary">
      {PRIMARY_TABS.map((tab, i) => (
        <button
          key={tab.id}
          onClick={() => onRoute({ top: tab.id })}
          className="run-nav-item"
          aria-current={route.top === tab.id ? 'page' : undefined}
          title={tab.description}
        >
          <i aria-hidden="true">{String(i + 1).padStart(2, '0')}</i>
          {tab.label}
        </button>
      ))}
      <div className="relative" data-scenarios-menu>
        <button
          onClick={() => setOpenMenu((s) => !s)}
          className="run-nav-item"
          aria-current={route.top === 'scenario' ? 'page' : undefined}
          aria-expanded={openMenu}
          aria-haspopup="menu"
        >
          <i aria-hidden="true">{pad(PRIMARY_TABS.length + 1)}</i>
          {scenarioActive || 'Scenarios'}
          {anyScenarioRunning && <Loader2 className="w-3 h-3 animate-spin" aria-hidden />}
          <ChevronDown className="w-3 h-3" aria-hidden />
        </button>
        {openMenu && (
          <div
            role="menu"
            className="absolute z-20 mt-1 right-0 min-w-[240px] rounded border border-border bg-bg shadow-lg py-1"
          >
            {SCENARIO_ITEMS.map((s) => (
              <button
                key={s.runType}
                role="menuitem"
                onClick={() => {
                  onRoute({ top: 'scenario', runType: s.runType })
                  setOpenMenu(false)
                }}
                className="w-full text-left px-3 py-2 text-sm hover:bg-surface-1 flex items-center justify-between gap-3"
              >
                <span>
                  <span className="block">{s.label}</span>
                  <span className="block text-content-subtle text-xs">{s.description}</span>
                </span>
                {activeByType[s.runType] && <Loader2 className="w-3 h-3 animate-spin shrink-0" aria-hidden />}
              </button>
            ))}
          </div>
        )}
      </div>
    </nav>
  )
}

function App() {
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [gameweekError, setGameweekError] = useState<string | null>(null)
  // One string, updated once a second — the old header rendered eight glowing
  // gradient tiles and repainted all of them on every tick.
  const [countdown, setCountdown] = useState<string | null>(null)
  const [route, setRoute] = useState<Route>(() => hashToRoute(window.location.hash))

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

  // Sync URL hash when the route changes
  useEffect(() => {
    const expectedHash = routeToHash(route)
    const currentHash = window.location.hash.slice(1)
    if (currentHash !== expectedHash) {
      const newUrl = expectedHash === '' ? window.location.pathname : `#${expectedHash}`
      window.history.pushState(null, '', newUrl)
    }
  }, [route])

  // Listen for browser back/forward navigation
  useEffect(() => {
    const handleNavigation = () => setRoute(hashToRoute(window.location.hash))
    window.addEventListener('popstate', handleNavigation)
    window.addEventListener('hashchange', handleNavigation)
    return () => {
      window.removeEventListener('popstate', handleNavigation)
      window.removeEventListener('hashchange', handleNavigation)
    }
  }, [])

  const openScenario = (runType: HermesRunType) => setRoute({ top: 'scenario', runType })

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

      {/* ---------- primary nav: four top-level views + Scenarios ---------- */}
      <TopNav route={route} onRoute={setRoute} activeByType={hermes.activeByType} />

      <main className="flex-1">
        {route.top === 'squad' && (
          <ThisWeekTab
            view="tracked"
            onViewChange={(rt) => setRoute({ top: 'scenario', runType: rt })}
            runs={hermes.runs}
            activeByType={hermes.activeByType}
            errors={hermes.errors}
            status={hermes.status}
            avgDurationMs={durationStats['hermes_run']?.avg_duration_ms ?? null}
            onStart={(rt, force, fplTeamId) => hermes.startRun(rt, force, fplTeamId)}
          />
        )}
        {route.top === 'this_week' && (
          <ThisWeekTab
            view="briefing"
            onViewChange={(rt) => setRoute({ top: 'scenario', runType: rt })}
            runs={hermes.runs}
            activeByType={hermes.activeByType}
            errors={hermes.errors}
            status={hermes.status}
            avgDurationMs={durationStats['hermes_run']?.avg_duration_ms ?? null}
            onStart={(rt, force, fplTeamId) => hermes.startRun(rt, force, fplTeamId)}
          />
        )}
        {route.top === 'season' && <SeasonView onOpenScenario={openScenario} />}
        {route.top === 'hermes' && <HermesAccountability />}
        {route.top === 'scenario' && route.runType && (
          <ThisWeekTab
            view={route.runType}
            onViewChange={(rt) => setRoute({ top: 'scenario', runType: rt })}
            runs={hermes.runs}
            activeByType={hermes.activeByType}
            errors={hermes.errors}
            status={hermes.status}
            avgDurationMs={durationStats['hermes_run']?.avg_duration_ms ?? null}
            onStart={(rt, force, fplTeamId) => hermes.startRun(rt, force, fplTeamId)}
          />
        )}
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
