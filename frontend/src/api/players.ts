/**
 * Players Search API
 */

import { apiRequest } from './client'
import type { Player } from '../types'

export interface PlayerSearchResult {
  id: number
  name: string
  full_name?: string
  team: string
  position: string
  price: number
  predicted?: number
  form?: number
  total_points?: number
  ownership?: number
  rotation_risk?: string
  european_comp?: string
}

export interface PlayersSearchResponse {
  players: PlayerSearchResult[]
}

/**
 * Search for players by name and/or position
 */
export async function searchPlayers(
  query: string = '',
  position?: string,
  limit: number = 50
): Promise<PlayersSearchResponse> {
  let url = `/api/players/search?q=${encodeURIComponent(query)}&limit=${limit}`
  if (position) url += `&position=${position}`
  return apiRequest<PlayersSearchResponse>(url)
}

