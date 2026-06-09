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
