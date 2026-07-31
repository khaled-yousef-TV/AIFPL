/**
 * Background Tasks API
 *
 * Tasks are created and updated by the backend only (Hermes runs, nightly
 * jobs); the frontend is a read-only viewer.
 */

import { apiFetch } from './client'
import type { Task } from '../types'

export interface TaskDurationStats {
  [taskType: string]: { avg_duration_ms: number; samples: number }
}

export interface TasksResponse {
  tasks: Task[]
  duration_stats: TaskDurationStats
}

/**
 * Fetch tasks plus per-type average durations.
 * include_old=true returns recent history (newest first, capped by limit),
 * not just the last 5 minutes.
 */
export async function fetchTasks(includeOld = true, limit = 30): Promise<TasksResponse> {
  const res = await apiFetch(`/api/tasks?include_old=${includeOld}&limit=${limit}`)
  if (!res.ok) {
    return { tasks: [], duration_stats: {} }
  }
  const data = await res.json()
  return { tasks: data.tasks || [], duration_stats: data.duration_stats || {} }
}
