/**
 * Predictions, Top Picks, and Differentials API
 */

import { apiRequest } from './client'
import type { Player } from '../types'

export interface TopPicksResponse {
  [position: string]: Player[]
}

export interface DifferentialsResponse {
  differentials: Player[]
}

/**
 * Fetch top picks grouped by position
 */
export async function fetchTopPicks(): Promise<TopPicksResponse> {
  return apiRequest<TopPicksResponse>('/api/top-picks')
}

/**
 * Fetch differential players (low ownership, high potential)
 */
export async function fetchDifferentials(
  maxOwnership: number = 10.0,
  topN: number = 10
): Promise<DifferentialsResponse> {
  return apiRequest<DifferentialsResponse>(
    `/api/differentials?max_ownership=${maxOwnership}&top_n=${topN}`
  )
}

/**
 * Fetch player predictions
 */
export async function fetchPredictions(
  position?: number,
  topN: number = 100
): Promise<{ predictions: Player[] }> {
  let url = `/api/predictions?top_n=${topN}`
  if (position) url += `&position=${position}`
  return apiRequest<{ predictions: Player[] }>(url)
}

