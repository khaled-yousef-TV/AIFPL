/**
 * Hermes Orchestrator API
 */

import { apiRequest } from './client'

export interface HermesStatus {
  hermes_enabled: boolean
  llm_configured: boolean
  model: string | null
  daily_briefing: boolean
  search_provider: string
  news_agent_enabled: boolean
}

export interface AgentReport {
  agent: string
  version: string
  gameweek: number
  generated_at: string
  status: 'ok' | 'degraded' | 'error'
  elapsed_ms: number
  summary: string
  payload: Record<string, any>
}

export interface SignalsResponse {
  gameweek: number
  agents_run: string[]
  reports: Record<string, AgentReport>
}

export type HermesRunType =
  | 'briefing'
  | 'squad'
  | 'wildcard'
  | 'free_hit'
  | 'triple_captain'
  | 'differentials'
  | 'my_team'
  | 'tracked'   // pseudo run type: renders /api/tracked-squad, not a backend Hermes run

export interface HermesRun {
  run_id: string
  gameweek: number
  run_type: HermesRunType
  status: 'pending' | 'running' | 'completed' | 'degraded' | 'failed'
  fpl_team_id: number | null
  signals: Record<string, AgentReport> | null
  adjustments: Record<string, any> | null
  result: Record<string, any> | null
  narrative: string | null
  error: string | null
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  created_at: string | null
  completed_at: string | null
}

export interface StartRunResponse {
  task_id: string | null
  run_id: string
  cached: boolean
}

export async function fetchHermesStatus(): Promise<HermesStatus> {
  return apiRequest<HermesStatus>('/api/hermes/status')
}

export interface LlmProvidersResponse {
  /** null = legacy single-provider LLM_* config */
  active: string | null
  active_model: string | null
  providers: Array<{ id: string; model: string | null; configured: boolean }>
}

export async function fetchLlmProviders(): Promise<LlmProvidersResponse> {
  return apiRequest<LlmProvidersResponse>('/api/hermes/providers')
}

export async function setLlmProvider(provider: string): Promise<{ active: string; active_model: string | null }> {
  return apiRequest('/api/hermes/provider', {
    method: 'POST',
    body: JSON.stringify({ provider }),
  })
}

export async function fetchSignals(topN: number = 40, agents?: string[]): Promise<SignalsResponse> {
  const params = new URLSearchParams({ top_n: String(topN) })
  if (agents?.length) params.set('agents', agents.join(','))
  return apiRequest<SignalsResponse>(`/api/hermes/signals?${params}`)
}

export async function startHermesRun(
  runType: HermesRunType,
  options: { fplTeamId?: number; force?: boolean } = {},
): Promise<StartRunResponse> {
  return apiRequest<StartRunResponse>('/api/hermes/run', {
    method: 'POST',
    body: JSON.stringify({
      run_type: runType,
      fpl_team_id: options.fplTeamId ?? null,
      force: options.force ?? false,
    }),
  })
}

export async function fetchHermesRun(runId: string): Promise<HermesRun> {
  return apiRequest<HermesRun>(`/api/hermes/runs/${runId}`)
}

export async function fetchLatestHermesRun(runType?: HermesRunType): Promise<HermesRun> {
  const params = runType ? `?run_type=${runType}` : ''
  return apiRequest<HermesRun>(`/api/hermes/latest${params}`)
}

/** Light payload for a pending/running run, with the task's progress joined in. */
export interface ActiveHermesRun {
  run_id: string
  run_type: HermesRunType
  gameweek: number
  status: 'pending' | 'running'
  progress: number
  created_at: string | null
}

export async function fetchLatestAllHermesRuns(): Promise<Record<string, HermesRun | null>> {
  const res = await apiRequest<{ runs: Record<string, HermesRun | null> }>('/api/hermes/latest-all')
  return res.runs
}

export async function fetchActiveHermesRuns(): Promise<ActiveHermesRun[]> {
  const res = await apiRequest<{ active: ActiveHermesRun[] }>('/api/hermes/active')
  return res.active
}

export interface CalibrationProfile {
  runs_scored: number
  action_hit_rates: Record<string, number>
  action_samples: Record<string, number>
  captain_regret_avg: number | null
  trust_weights: Record<string, number>
}

export interface CalibrationResponse {
  model: string | null
  profile: CalibrationProfile
  lessons: Array<{
    id: number
    gameweek_learned: number
    category: string
    lesson: string
    weight: number
    scope?: 'game' | 'model'
    model?: string | null
  }>
}

export interface TransferPlan {
  recommendation: 'transfer' | 'hold'
  reason: string
  expected_gain: number | null
  hit_cost: number
}

export interface ChipProjection {
  gameweek: number | null
  confidence: 'low' | 'medium' | 'high'
  reason: string
  requires_transfers: boolean
}

export interface BacktestSummary {
  season: string
  summary: {
    gameweeks_scored: number
    // numeric stats plus nested head-to-head objects (anchored_vs_*, blend_vs_*)
    captaincy: Record<string, any>
    form_signal: Record<string, number>
    consistency_signal: Record<string, number>
    verdict?: {
      captaincy_beats_naive: boolean
      captaincy_matches_naive?: boolean
      captaincy_beats_template?: boolean
      form_signal_real: boolean
      consistency_signal_real: boolean
      has_measurable_edge: boolean
      caveat: string
      notes: string[]
    }
  }
}

export async function fetchCalibration(): Promise<CalibrationResponse> {
  return apiRequest<CalibrationResponse>('/api/hermes/calibration')
}

export async function fetchArchiveStatus(): Promise<{ seasons: Array<{ season: string; players: number }> }> {
  return apiRequest('/api/hermes/archive-status')
}

export async function fetchBacktest(season: string): Promise<BacktestSummary> {
  return apiRequest<BacktestSummary>(`/api/hermes/backtest?season=${encodeURIComponent(season)}`)
}
