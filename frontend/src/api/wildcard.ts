/**
 * Wildcard Trajectory API
 * 
 * Fetches 8-GW optimal squad trajectory using hybrid LSTM-XGBoost model
 */

import { apiRequest } from './client'
import type { WildcardTrajectory } from '../types'

export interface WildcardTrajectoryRequest {
  budget?: number
  horizon?: number
  current_squad?: Array<{
    id: number
    name: string
    position: string
    price: number
  }>
}

/**
 * Get optimal 8-GW wildcard trajectory
 * 
 * Uses hybrid LSTM+XGBoost model with:
 * - Weighted formula: 0.7×LSTM + 0.3×XGBoost
 * - FDR adjustment
 * - Transfer decay factor
 * - MILP optimizer
 */
export async function fetchWildcardTrajectory(
  request: WildcardTrajectoryRequest = {}
): Promise<WildcardTrajectory> {
  return apiRequest<WildcardTrajectory>('/api/chips/wildcard-trajectory', {
    method: 'POST',
    body: JSON.stringify({
      budget: request.budget ?? 100.0,
      horizon: request.horizon ?? 8,
      current_squad: request.current_squad ?? null
    }),
  })
}

/**
 * Get wildcard trajectory with query params (GET endpoint)
 */
export async function fetchWildcardTrajectoryGet(
  budget: number = 100.0,
  horizon: number = 8
): Promise<WildcardTrajectory> {
  return apiRequest<WildcardTrajectory>(
    `/api/chips/wildcard-trajectory?budget=${budget}&horizon=${horizon}`
  )
}

