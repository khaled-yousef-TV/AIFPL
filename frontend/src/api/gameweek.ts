/**
 * Gameweek and Selected Teams API
 */

import { apiRequest } from './client'
import type { GameWeekInfo, SelectedTeam, SuggestedSquad } from '../types'

export interface SelectedTeamsResponse {
  teams: Record<number, {
    gameweek: number
    squad: SuggestedSquad
    saved_at: string
  }>
}

/**
 * Fetch current and next gameweek information
 */
export async function fetchGameweek(): Promise<GameWeekInfo> {
  return apiRequest<GameWeekInfo>('/api/gameweek')
}

/**
 * Fetch all selected teams (suggested squads for each gameweek)
 */
export async function fetchSelectedTeams(): Promise<SelectedTeamsResponse> {
  return apiRequest<SelectedTeamsResponse>('/api/selected-teams')
}

/**
 * Fetch selected team for a specific gameweek
 */
export async function fetchSelectedTeam(gameweek: number): Promise<{ team: SelectedTeam | null }> {
  return apiRequest<{ team: SelectedTeam | null }>(`/api/selected-teams/${gameweek}`)
}

