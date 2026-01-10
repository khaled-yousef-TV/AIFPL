/**
 * Transfer Suggestions and Wildcard API
 */

import { apiRequest } from './client'
import type { SquadPlayer, TransferSuggestion } from '../types'

export interface TransferRequest {
  squad: SquadPlayer[]
  bank: number
  free_transfers: number
}

export interface TransferSuggestionsResponse {
  suggestions: TransferSuggestion[]
  squad_analysis?: any[]
}

export interface WildcardResponse {
  transfers_out: any[]
  transfers_in: any[]
  kept_players: any[]
  resulting_squad: {
    squad: any[]
    formation: string | Record<string, number>
  }
  before_total_points: number
  after_total_points: number
  total_points_gain: number
  total_cost: number
  individual_breakdowns: any[]
  combined_rationale?: string
  before_formation?: string
}

/**
 * Get transfer suggestions for 1-3 transfers
 */
export async function fetchTransferSuggestions(
  request: TransferRequest
): Promise<TransferSuggestionsResponse> {
  return apiRequest<TransferSuggestionsResponse>('/api/transfer-suggestions', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

/**
 * Get wildcard/rebuild plan for 4+ transfers
 */
export async function fetchWildcard(
  request: TransferRequest
): Promise<WildcardResponse> {
  return apiRequest<WildcardResponse>('/api/wildcard', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export interface ImportFplTeamResponse {
  squad: SquadPlayer[]
  bank: number
  team_name: string
  gameweek: number
  team_value: number
}

/**
 * Import a team from FPL by team ID
 */
export async function importFplTeam(
  teamId: number,
  gameweek?: number
): Promise<ImportFplTeamResponse> {
  let url = `/api/import-fpl-team/${teamId}`
  if (gameweek) url += `?gameweek=${gameweek}`
  return apiRequest<ImportFplTeamResponse>(url)
}

