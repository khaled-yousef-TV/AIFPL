/**
 * Background-task state, read straight from the backend Task table.
 *
 * The backend is the only writer (Hermes runs, nightly jobs create and
 * update their own Task rows), so timings shown in the UI are the real
 * server-side start/finish times — no client-side progress simulation.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTasks, TaskDurationStats } from '../api/tasks'
import type { Task } from '../types'

const ACTIVE_POLL_MS = 3000
const IDLE_POLL_MS = 15000

export interface TasksState {
  tasks: Task[]
  durationStats: TaskDurationStats
  anyActive: boolean
  reload: () => Promise<void>
}

export function useTasks(): TasksState {
  const [tasks, setTasks] = useState<Task[]>([])
  const [durationStats, setDurationStats] = useState<TaskDurationStats>({})
  const loadingRef = useRef(false)

  const reload = useCallback(async () => {
    if (loadingRef.current) return
    loadingRef.current = true
    try {
      const res = await fetchTasks(true, 30)
      setTasks(res.tasks)
      setDurationStats(res.duration_stats)
    } finally {
      loadingRef.current = false
    }
  }, [])

  const anyActive = tasks.some((t) => t.status === 'running' || t.status === 'pending')

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    const interval = setInterval(reload, anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(interval)
  }, [anyActive, reload])

  return { tasks, durationStats, anyActive, reload }
}
