/**
 * Global Hermes run state.
 *
 * Lives at the App level so run/loading state survives tab switches — the
 * old HermesTab kept it in component state, which let users fire duplicate
 * runs after clicking away. The backend is the source of truth: we poll
 * /api/hermes/active, so runs started elsewhere (the nightly sweep, another
 * browser tab) show up too, and a 409 on start simply attaches to the run
 * already in flight.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActiveHermesRun,
  HermesRun,
  HermesRunType,
  HermesStatus,
  fetchActiveHermesRuns,
  fetchHermesRun,
  fetchHermesStatus,
  fetchLatestAllHermesRuns,
  startHermesRun,
} from '../api/hermes'

export interface HermesRunTypeInfo {
  value: HermesRunType
  label: string
  shortLabel: string
  description: string
}

/**
 * Nav ordering follows the four-view intent (see TRACKED_SQUAD_PLAN.md):
 *   Squad-shaped answers first (tracked benchmark, then your real team),
 *   the weekly briefing (the evidence), the chip/scenario tools last.
 *
 * "Tracked" is the persistent squad Hermes manages week to week — pure
 * benchmark. "My Team" answers the same question against your connected
 * FPL squad. "Briefing" is the underlying agent-signal view that both feed
 * off of. Everything after that is a scenario tool.
 */
export const HERMES_RUN_TYPES: HermesRunTypeInfo[] = [
  { value: 'tracked', label: 'Tracked Squad', shortLabel: 'Tracked', description: 'The persistent Hermes-driven benchmark squad, week by week' },
  { value: 'my_team', label: 'My Team', shortLabel: 'Mine', description: 'Personalized advice for your imported FPL team' },
  { value: 'briefing', label: 'Weekly Briefing', shortLabel: 'Briefing', description: 'Full analysis: squad, captaincy, chips, differentials' },
  { value: 'squad', label: 'Best Squad', shortLabel: 'Squad', description: 'Optimal 15-man squad with Hermes adjustments' },
  { value: 'wildcard', label: 'Wildcard', shortLabel: 'WC', description: 'Should you wildcard now? Full rebuild plan' },
  { value: 'free_hit', label: 'Free Hit', shortLabel: 'FH', description: 'One-week-only optimal squad' },
  { value: 'triple_captain', label: 'Triple Captain', shortLabel: 'TC', description: 'Highest-ceiling captaincy for TC' },
  { value: 'differentials', label: 'Differentials', shortLabel: 'Diffs', description: 'Low-ownership picks with strong signals' },
]

/** Pseudo run types have no backend Hermes run — skip run polling/start for them. */
export const PSEUDO_RUN_TYPES: HermesRunType[] = ['tracked']

const ACTIVE_POLL_MS = 3000
// Idle polling keeps nightly/server-started runs visible without a reload
const IDLE_POLL_MS = 30000

export interface HermesState {
  status: HermesStatus | null
  /** Latest completed/degraded run per run type */
  runs: Record<string, HermesRun | null>
  /** In-flight run per run type */
  activeByType: Record<string, ActiveHermesRun>
  anyRunning: boolean
  errors: Record<string, string | null>
  startRun: (runType: HermesRunType, force?: boolean, fplTeamId?: number) => Promise<void>
  refreshLatest: () => Promise<void>
}

export function useHermes(): HermesState {
  const [status, setStatus] = useState<HermesStatus | null>(null)
  const [runs, setRuns] = useState<Record<string, HermesRun | null>>({})
  const [active, setActive] = useState<ActiveHermesRun[]>([])
  const [errors, setErrors] = useState<Record<string, string | null>>({})
  const activeRef = useRef<ActiveHermesRun[]>([])
  activeRef.current = active

  const refreshLatest = useCallback(async () => {
    try {
      setRuns(await fetchLatestAllHermesRuns())
    } catch {
      // backend unreachable — keep whatever we have
    }
  }, [])

  const refreshActive = useCallback(async () => {
    try {
      const next = await fetchActiveHermesRuns()
      const stillActive = new Set(next.map((r) => r.run_id))
      const finished = activeRef.current.filter((r) => !stillActive.has(r.run_id))
      setActive(next)
      // A run that left the active set just hit a terminal state — pull its
      // final result so the tab updates without waiting for the next reload.
      for (const f of finished) {
        try {
          const done = await fetchHermesRun(f.run_id)
          setRuns((s) => ({ ...s, [done.run_type]: done }))
        } catch {
          // fall back to the periodic latest refresh
        }
      }
    } catch {
      // transient polling errors are fine; keep trying
    }
  }, [])

  useEffect(() => {
    fetchHermesStatus().then(setStatus).catch(() => setStatus(null))
    refreshLatest()
    refreshActive()
  }, [refreshLatest, refreshActive])

  const anyRunning = active.length > 0

  useEffect(() => {
    const interval = setInterval(refreshActive, anyRunning ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(interval)
  }, [anyRunning, refreshActive])

  const startRun = useCallback(
    async (runType: HermesRunType, force = false, fplTeamId?: number) => {
      // Pseudo run types (tracked squad) have no backend Hermes run to start —
      // they render /api/tracked-squad + the linked my_team run instead.
      if (PSEUDO_RUN_TYPES.includes(runType)) return
      if (activeRef.current.some((r) => r.run_type === runType)) return
      setErrors((s) => ({ ...s, [runType]: null }))
      try {
        const started = await startHermesRun(runType, { force, fplTeamId })
        if (started.cached) {
          // Today's run already exists — just show it
          const run = await fetchHermesRun(started.run_id)
          setRuns((s) => ({ ...s, [runType]: run }))
          return
        }
        // Show it immediately; the active poll takes over from here
        setActive((a) => [
          ...a.filter((r) => r.run_type !== runType),
          {
            run_id: started.run_id,
            run_type: runType,
            gameweek: 0,
            status: 'pending',
            progress: 0,
            created_at: new Date().toISOString(),
          },
        ])
      } catch (e: any) {
        // 409 means a run for this type is already in flight (e.g. the
        // nightly sweep) — the active poll below will attach to it.
        setErrors((s) => ({ ...s, [runType]: e?.message || 'Failed to start Hermes run' }))
        refreshActive()
      }
    },
    [refreshActive],
  )

  const activeByType: Record<string, ActiveHermesRun> = {}
  for (const run of active) activeByType[run.run_type] = run

  return { status, runs, activeByType, anyRunning, errors, startRun, refreshLatest }
}
