/**
 * Task-related type definitions for background jobs
 */

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

export type TaskType = 
  | 'daily_snapshot' 
  | 'triple_captain' 
  | 'refresh_picks' 
  | 'refresh_differentials' 
  | 'refresh_transfers' 
  | 'refresh_wildcard'

export interface Task {
  id: string
  type: TaskType
  title: string
  description: string
  status: TaskStatus
  progress: number // 0-100
  createdAt: number
  completedAt?: number
  error?: string
}

export interface Notification {
  id: string
  type: 'success' | 'error'
  title: string
  message: string
  timestamp: number
}

export interface TaskStartedModal {
  taskId: string
  title: string
}

