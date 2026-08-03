/**
 * Tracked-squad API — the persistent Hermes-driven benchmark squad.
 */

import { apiRequest } from './client'

export interface TrackedSquadState {
  gameweek: number
  players: number[]
  purchase_prices: Record<string, number>
  captain_id: number
  vice_id: number | null
  bank: number
  free_transfers: number
  chips_used: string[]
  chip_active: string | null
  created_at: string | null
}

export interface TrackedSquadLedgerRow {
  gameweek: number
  points_scored: number | null
  captain_points: number | null
  bench_points: number | null
  transfer_cost: number
  transfers_made: any[]
  autosubs: any[]
  average_score: number | null
  hermes_run_id: string | null
  scored_at: string | null
}

export interface TrackedSquadPlayerInfo {
  name: string
  team: string
  position: 'GK' | 'DEF' | 'MID' | 'FWD' | '?'
  price: number | null
  status: string
}

export interface TrackedSquadResponse {
  seeded: boolean
  state?: TrackedSquadState
  history?: TrackedSquadState[]
  ledger?: TrackedSquadLedgerRow[]
  /** id (as string) -> {name, team, position, price, status} for the current squad */
  players?: Record<string, TrackedSquadPlayerInfo>
  /** Squad-shaped payload matching `result.squad` from a Hermes run, ready for PitchView. */
  squad?: any
}

export async function fetchTrackedSquad(): Promise<TrackedSquadResponse> {
  return apiRequest<TrackedSquadResponse>('/api/tracked-squad')
}

export async function seedTrackedSquad(): Promise<{ seeded: true; state: TrackedSquadState }> {
  return apiRequest('/api/tracked-squad/seed', { method: 'POST' })
}

export async function resetTrackedSquad(): Promise<{ deleted: number }> {
  return apiRequest('/api/tracked-squad/reset', { method: 'POST' })
}
