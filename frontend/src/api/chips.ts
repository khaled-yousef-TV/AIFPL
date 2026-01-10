/**
 * Chips API (Triple Captain, etc.)
 */

import { apiRequest, apiFetch } from './client'

export interface TripleCaptainCandidate {
  id: number
  name: string
  team: string
  position: string
  price: number
  predicted?: number
  haul_probability?: number
  opponent?: string
  is_home?: boolean
  form?: number
  ownership?: number
}

export interface TripleCaptainResponse {
  recommendations: TripleCaptainCandidate[]
  next_deadline?: string
  current_gameweek?: number
  gameweek?: number
}

/**
 * Fetch triple captain recommendations
 */
export async function fetchTripleCaptain(
  topN: number = 10,
  signal?: AbortSignal
): Promise<TripleCaptainResponse> {
  const response = await apiFetch(`/api/chips/triple-captain?top_n=${topN}`, {
    signal,
  })
  
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  
  return response.json()
}

/**
 * Check if triple captain data is stale
 */
export async function checkTripleCaptainStatus(): Promise<{
  is_stale: boolean
  last_update?: string
}> {
  return apiRequest<{ is_stale: boolean; last_update?: string }>(
    '/api/chips/triple-captain/status'
  )
}

