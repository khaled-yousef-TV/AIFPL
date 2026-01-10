/**
 * FPL Teams API (saved team IDs)
 */

import { apiRequest, apiFetch } from './client'
import type { SavedFplTeam } from '../types'

export interface FplTeamsResponse {
  teams: Array<{
    teamId: number
    teamName: string
    lastImported?: string
  }>
}

export interface SaveFplTeamResponse {
  success: boolean
  message: string
}

/**
 * Fetch all saved FPL team IDs
 */
export async function fetchSavedFplTeams(): Promise<SavedFplTeam[]> {
  const res = await apiFetch('/api/fpl-teams')
  if (!res.ok) {
    console.error(`Failed to load saved FPL teams: HTTP ${res.status}`)
    return []
  }
  
  const data: FplTeamsResponse = await res.json()
  
  if (data.teams && Array.isArray(data.teams)) {
    return data.teams.map((t) => ({
      teamId: t.teamId,
      teamName: t.teamName,
      lastImported: t.lastImported ? new Date(t.lastImported).getTime() : Date.now()
    }))
  }
  
  console.warn('Unexpected response format from fpl-teams endpoint:', data)
  return []
}

/**
 * Save an FPL team ID to the database
 */
export async function saveFplTeam(
  teamId: number, 
  teamName: string
): Promise<SaveFplTeamResponse> {
  return apiRequest<SaveFplTeamResponse>('/api/fpl-teams', {
    method: 'POST',
    body: JSON.stringify({ team_id: teamId, team_name: teamName }),
  })
}

