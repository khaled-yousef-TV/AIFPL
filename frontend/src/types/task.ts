/**
 * Task-related type definitions for background jobs.
 *
 * Tasks are created and updated by the backend only; the shape mirrors the
 * serialization in backend/database/crud.py (_task_to_dict).
 */

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface Task {
  id: string
  type: string // e.g. 'hermes_run', 'season_archive', 'daily_snapshot'
  title: string
  description: string | null
  status: TaskStatus
  progress: number // 0-100
  createdAt: number // epoch ms (UTC)
  completedAt?: number | null
  error?: string | null
}
