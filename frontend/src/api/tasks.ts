/**
 * Background Tasks API
 */

import { apiRequest, apiFetch } from './client'
import type { Task } from '../types'

export interface TasksResponse {
  tasks: Task[]
}

export interface TaskResponse {
  task: Task
}

/**
 * Fetch all tasks
 */
export async function fetchTasks(includeOld: boolean = false): Promise<TasksResponse> {
  const res = await apiFetch(`/api/tasks?include_old=${includeOld}`)
  if (!res.ok) {
    return { tasks: [] }
  }
  return res.json()
}

/**
 * Create a new task
 */
export async function createTask(task: Omit<Task, 'completedAt' | 'error'>): Promise<void> {
  await apiFetch('/api/tasks', {
    method: 'POST',
    body: JSON.stringify(task),
  })
}

/**
 * Update an existing task
 */
export async function updateTask(
  taskId: string, 
  updates: Partial<Task>
): Promise<void> {
  await apiFetch(`/api/tasks/${taskId}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  })
}

/**
 * Delete a task
 */
export async function deleteTask(taskId: string): Promise<void> {
  await apiFetch(`/api/tasks/${taskId}`, {
    method: 'DELETE',
  })
}

/**
 * Trigger daily snapshot update
 */
export async function triggerDailySnapshot(): Promise<{ message: string }> {
  return apiRequest<{ message: string }>('/api/daily-snapshot/update', {
    method: 'POST',
  })
}

