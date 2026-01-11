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

export interface WildcardTrajectoryTaskResponse {
  task_id: string
  status: string
  message: string
}

/**
 * Submit wildcard trajectory calculation (returns task ID)
 * 
 * Creates an async task and returns immediately with a task ID.
 * Use getWildcardTrajectoryResult() to fetch the result once the task completes.
 */
export async function submitWildcardTrajectory(
  request: WildcardTrajectoryRequest = {}
): Promise<WildcardTrajectoryTaskResponse> {
  return apiRequest<WildcardTrajectoryTaskResponse>('/api/chips/wildcard-trajectory', {
    method: 'POST',
    body: JSON.stringify({
      budget: request.budget ?? 100.0,
      horizon: request.horizon ?? 8,
      current_squad: request.current_squad ?? null
    }),
  })
}

/**
 * Get wildcard trajectory result by task ID
 */
export async function getWildcardTrajectoryResult(taskId: string): Promise<WildcardTrajectory> {
  return apiRequest<WildcardTrajectory>(`/api/chips/wildcard-trajectory/${taskId}`)
}

/**
 * Get optimal 8-GW wildcard trajectory (legacy - synchronous)
 * 
 * @deprecated Use submitWildcardTrajectory() and getWildcardTrajectoryResult() for async task-based pattern
 */
export async function fetchWildcardTrajectory(
  request: WildcardTrajectoryRequest = {}
): Promise<WildcardTrajectory> {
  // Submit task and wait for result (for backward compatibility)
  const taskResponse = await submitWildcardTrajectory(request)
  return getWildcardTrajectoryResult(taskResponse.task_id)
}

/**
 * Get wildcard trajectory with query params (GET endpoint)
 * @deprecated Use submitWildcardTrajectory() for async task-based pattern
 */
export async function fetchWildcardTrajectoryGet(
  budget: number = 100.0,
  horizon: number = 8
): Promise<WildcardTrajectory> {
  return fetchWildcardTrajectory({ budget, horizon })
}

