import { useEffect, useState } from 'react'
import { Brain, Loader2, RefreshCw, TrendingUp } from 'lucide-react'

import type { GameWeekInfo } from './types'
import { FPLLogo } from './components'
import { HermesInsights, ThisWeekTab } from './tabs'
import { HERMES_RUN_TYPES, useHermes } from './hooks/useHermes'
import { useTasks } from './hooks/useTasks'
import type { HermesRunType } from './api/hermes'

// In production set this to your hosted backend, e.g. https://api.fplai.nl
// In local dev it defaults to http://localhost:8001
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8001'

// Two destinations: this week's Hermes report, and how it has performed.
// Everything else (run types, tasks) lives inside those.
const NAV_TABS = [
  { id: 'thisweek', icon: Brain, label: 'This Week', shortLabel: 'Week', color: 'text-purple-400' },
  { id: 'insights', icon: TrendingUp, label: 'Track Record', shortLabel: 'Record', color: 'text-green-400' },
]

const RUN_TYPE_IDS = HERMES_RUN_TYPES.map((rt) => rt.value as string)
const DEFAULT_VIEW: HermesRunType = 'briefing'

/** Map a URL hash to {tab, view}: run-type hashes deep-link into This Week. */
function parseHash(hash: string): { tab: string; view: HermesRunType } {
  if (hash === 'insights') return { tab: 'insights', view: DEFAULT_VIEW }
  if (RUN_TYPE_IDS.includes(hash)) return { tab: 'thisweek', view: hash as HermesRunType }
  return { tab: 'thisweek', view: DEFAULT_VIEW }
}

function App() {
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [gameweekError, setGameweekError] = useState<string | null>(null)
  const [countdown, setCountdown] = useState<{ days: number; hours: number; minutes: number; seconds: number } | null>(null)
  const [{ tab: activeTab, view }, setLocation] = useState(() => parseHash(window.location.hash.slice(1)))

  const hermes = useHermes()
  const { durationStats, anyActive: anyTaskActive } = useTasks()

  const setActiveTab = (tab: string) => setLocation((s) => ({ ...s, tab }))
  const setView = (v: HermesRunType) => setLocation({ tab: 'thisweek', view: v })

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

  // Sync URL hash when the location changes
  useEffect(() => {
    const expectedHash =
      activeTab === 'insights' ? 'insights' : view === DEFAULT_VIEW ? '' : view
    const currentHash = window.location.hash.slice(1)
    if (currentHash !== expectedHash) {
      const newUrl = expectedHash === '' ? window.location.pathname : `#${expectedHash}`
      window.history.pushState(null, '', newUrl)
    }
  }, [activeTab, view])

  // Listen for browser back/forward navigation
  useEffect(() => {
    const handleNavigation = () => setLocation(parseHash(window.location.hash.slice(1)))
    window.addEventListener('popstate', handleNavigation)
    window.addEventListener('hashchange', handleNavigation)
    return () => {
      window.removeEventListener('popstate', handleNavigation)
      window.removeEventListener('hashchange', handleNavigation)
    }
  }, [])

  // Countdown timer for gameweek deadline
  useEffect(() => {
    if (!gameweek?.next?.deadline) {
      setCountdown(null)
      return
    }

    const updateCountdown = () => {
      if (!gameweek?.next?.deadline) return
      const deadline = new Date(gameweek.next.deadline).getTime()
      const diff = deadline - Date.now()

      if (diff <= 0) {
        setCountdown({ days: 0, hours: 0, minutes: 0, seconds: 0 })
        return
      }

      setCountdown({
        days: Math.floor(diff / (1000 * 60 * 60 * 24)),
        hours: Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60)),
        minutes: Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60)),
        seconds: Math.floor((diff % (1000 * 60)) / 1000),
      })
    }

    updateCountdown()
    const interval = setInterval(updateCountdown, 1000)
    return () => clearInterval(interval)
  }, [gameweek?.next?.deadline])

  const refresh = async () => {
    await Promise.all([loadGameweek(), hermes.refreshLatest()])
  }

  const anyWorking = hermes.anyRunning || anyTaskActive

  // Off-season (gameweek loaded, no next deadline) must not look like loading
  const offSeason = !!gameweek && !gameweek.next
  const gwLabel = gameweek ? (gameweek.next?.id ? `GW${gameweek.next.id}` : 'Season finished') : 'Loading...'

  // Background activity chip shown in headers: tasks demoted from a tab to an indicator
  const workingChip = anyWorking && (
    <span
      className="flex items-center gap-1.5 text-xs text-[#00ff87]"
      title="Hermes is working in the background"
    >
      <Loader2 className="w-3.5 h-3.5 animate-spin" />
      <span className="hidden sm:inline">working…</span>
    </span>
  )

  return (
    <div className="min-h-screen bg-[#0f0f1a] text-white flex">
      {/* Left Sidebar Navigation - Desktop Only */}
      <aside className="hidden md:flex flex-col w-64 bg-[#1a1a2e] border-r border-[#2a2a4a] sticky top-0 h-screen overflow-y-auto">
        <div className="px-6 py-4 border-b border-[#2a2a4a]">
          <div className="flex items-center gap-3 h-10">
            <div className="w-10 h-10 bg-gradient-to-br from-[#38003c] to-[#00ff87] rounded-lg flex items-center justify-center shadow-lg border border-[#00ff87]/20 flex-shrink-0">
              <FPLLogo className="w-6 h-6" />
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="font-bold text-sm leading-tight">Hermes FPL</h1>
              <p className="text-[10px] text-gray-400 leading-tight">{gwLabel}</p>
            </div>
          </div>
        </div>
        <nav className="flex-1 p-2">
          {NAV_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 transition-colors ${
                activeTab === tab.id
                  ? 'bg-[#00ff87]/10 text-[#00ff87] border border-[#00ff87]/30'
                  : 'text-gray-400 hover:text-white hover:bg-[#1a1a2e]/50'
              }`}
            >
              <tab.icon className={`w-5 h-5 flex-shrink-0 ${activeTab === tab.id ? tab.color : ''}`} />
              <span className="text-sm font-medium">{tab.label}</span>
              {tab.id === 'thisweek' && anyWorking && (
                <Loader2 className="w-4 h-4 text-[#00ff87] animate-spin ml-auto flex-shrink-0" />
              )}
            </button>
          ))}
        </nav>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile Header */}
        <header className="md:hidden bg-[#1a1a2e] border-b border-[#2a2a4a] px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <button onClick={() => setView(DEFAULT_VIEW)} className="flex items-center gap-2">
              <div className="w-10 h-10 bg-gradient-to-br from-[#38003c] to-[#00ff87] rounded-lg flex items-center justify-center shadow-lg border border-[#00ff87]/20">
                <FPLLogo className="w-6 h-6" />
              </div>
              <div className="flex-1 min-w-0">
                <h1 className="font-bold text-sm">Hermes FPL</h1>
                {gameweek?.next && countdown ? (
                  <div className="flex items-center gap-1 mt-0.5">
                    <span className="text-[10px] text-gray-300 font-bold">GW{gameweek.next.id}</span>
                    <span className="text-[10px] text-gray-600 mx-0.5">•</span>
                    {countdown.days > 0 && (
                      <>
                        <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.5)]">{countdown.days}d</span>
                        <span className="text-[10px] text-gray-600">:</span>
                      </>
                    )}
                    <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.5)]">{String(countdown.hours).padStart(2, '0')}</span>
                    <span className="text-[10px] text-gray-600">:</span>
                    <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.5)]">{String(countdown.minutes).padStart(2, '0')}</span>
                    <span className="text-[10px] text-gray-600">:</span>
                    <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.6)] animate-pulse">{String(countdown.seconds).padStart(2, '0')}</span>
                  </div>
                ) : (
                  <p className={`text-[10px] text-gray-400 ${gameweek ? '' : 'animate-pulse'}`}>{gwLabel}</p>
                )}
              </div>
            </button>
            <div className="flex items-center gap-2">
              {workingChip}
              <button
                onClick={refresh}
                className="btn btn-secondary flex items-center gap-1 text-xs px-3 py-1.5"
              >
                <RefreshCw className="w-3 h-3" />
                <span>Refresh</span>
              </button>
            </div>
          </div>
        </header>

        {/* Mobile Navigation */}
        <nav className="md:hidden bg-[#1a1a2e]/50 border-b border-[#2a2a4a] px-4 overflow-x-auto scrollbar-hide">
          <div className="flex gap-1 min-w-max py-2">
            {NAV_TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1 px-3 py-2 border-b-2 transition-colors whitespace-nowrap ${
                  activeTab === tab.id
                    ? 'border-[#00ff87] text-white'
                    : 'border-transparent text-gray-400 hover:text-white'
                }`}
              >
                <tab.icon className="w-4 h-4 flex-shrink-0" />
                <span className="text-xs">{tab.shortLabel}</span>
              </button>
            ))}
          </div>
        </nav>

        {/* Desktop Header */}
        <header className="hidden md:block bg-[#1a1a2e] border-b border-[#2a2a4a] px-6 py-4">
          <div className="flex items-center justify-between h-10">
            <div className="flex items-center gap-4 h-full">
              {gameweek?.next && (
                <>
                  <div className="text-gray-300 font-bold text-lg tracking-wide flex items-center h-full">
                    GW{gameweek.next.id}
                  </div>
                  {countdown && (
                    <>
                      <div className="h-6 w-px bg-gradient-to-b from-transparent via-[#00ff87]/30 to-transparent flex items-center"></div>
                      <div className="flex items-center gap-2 h-full">
                        {countdown.days > 0 && (
                          <div className="flex items-center gap-1.5 h-full">
                            <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/30 border border-[#00ff87]/20 flex items-center justify-center">
                              <span className="relative z-10 drop-shadow-sm">{countdown.days}</span>
                            </div>
                            <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">d</span>
                          </div>
                        )}
                        <div className="flex items-center gap-1.5 h-full">
                          <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/30 border border-[#00ff87]/20 flex items-center justify-center">
                            <span className="relative z-10 drop-shadow-sm">{String(countdown.hours).padStart(2, '0')}</span>
                          </div>
                          <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">h</span>
                        </div>
                        <div className="flex items-center gap-1.5 h-full">
                          <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/30 border border-[#00ff87]/20 flex items-center justify-center">
                            <span className="relative z-10 drop-shadow-sm">{String(countdown.minutes).padStart(2, '0')}</span>
                          </div>
                          <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">m</span>
                        </div>
                        <div className="flex items-center gap-1.5 h-full">
                          <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/40 border border-[#00ff87]/30 flex items-center justify-center">
                            <span className="relative z-10 drop-shadow-sm">{String(countdown.seconds).padStart(2, '0')}</span>
                          </div>
                          <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">s</span>
                        </div>
                      </div>
                    </>
                  )}
                  {!countdown && <div className="text-gray-400 text-sm animate-pulse flex items-center h-full">Loading...</div>}
                </>
              )}
              {!gameweek?.next && (
                offSeason
                  ? <div className="text-gray-400 text-sm flex items-center h-full">Season finished — next season's fixtures TBC</div>
                  : gameweekError
                  ? <div className="text-red-400 text-sm flex items-center h-full">{gameweekError}</div>
                  : <div className="text-gray-400 text-sm animate-pulse flex items-center h-full">Loading...</div>
              )}
            </div>
            <div className="flex items-center gap-3 h-full">
              {workingChip}
              <button
                onClick={refresh}
                className="btn btn-secondary flex items-center gap-2 text-sm px-4 py-2 h-full"
              >
                <RefreshCw className="w-4 h-4" />
                <span>Refresh</span>
              </button>
            </div>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
          {activeTab === 'thisweek' && (
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
          )}

          {activeTab === 'insights' && <HermesInsights />}
        </main>

        {/* Footer */}
        <footer className="border-t border-[#2a2a4a] py-6 mt-12">
          <div className="max-w-6xl mx-auto px-6 text-center text-gray-500 text-sm">
            Hermes FPL • AI-powered predictions • Not affiliated with Premier League
          </div>
        </footer>
      </div>
    </div>
  )
}

export default App
