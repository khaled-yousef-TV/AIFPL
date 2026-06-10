import { useState, useEffect, useCallback, useRef } from 'react'

import type { Task, TaskStatus, TaskType } from '../types'

// Task-management cluster extracted from App.tsx.
// API_BASE is passed in so the hook uses exactly the same backend base URL as App.tsx.
export function useTasks(API_BASE: string) {
  // Task management
  const [tasks, setTasks] = useState<Task[]>([])
  const [notifications, setNotifications] = useState<Array<{ id: string; type: 'success' | 'error'; title: string; message: string; timestamp: number }>>([])
  const [taskStartedModal, setTaskStartedModal] = useState<{ taskId: string; title: string } | null>(null)

  // Ref to track if we need immediate poll (after task creation)
  const needsImmediatePollRef = useRef(false)

  // Ref to track previous task states for detecting completion
  const previousTasksRef = useRef<Task[]>([])

  const TASKS_KEY = 'fpl_tasks_v1' // Key for persisting tasks

  // Check if a running task actually completed by checking the backend
  const checkTaskCompletion = useCallback(async (task: Task): Promise<boolean> => {
    try {
      if (task.type === 'daily_snapshot') {
        // Check if a new snapshot exists after task start
        const res = await fetch(`${API_BASE}/api/selected-teams`).then(r => r.json())
        if (res.teams && res.teams.length > 0) {
          const latestTeam = res.teams[0]
          const snapshotTime = new Date(latestTeam.saved_at).getTime()
          return snapshotTime > task.createdAt
        }
      } else if (task.type === 'triple_captain') {
        // Check if recommendations exist
        const res = await fetch(`${API_BASE}/api/chips/triple-captain`).then(r => r.json())
        if (res && typeof res === 'object') {
          const hasRecs = (res.recommendations && Object.keys(res.recommendations).length > 0) ||
                        (res.gameweeks && Array.isArray(res.gameweeks) && res.gameweeks.length > 0) ||
                        (res.gameweek && typeof res.gameweek === 'object')
          return !!hasRecs
        }
      }
    } catch (err) {
      console.error('Error checking task completion:', err)
    }
    return false
  }, [])

  // Check backend for recent operations and create tasks for them
  const checkBackendForRecentTasks = async (): Promise<Task[]> => {
    const recentTasks: Task[] = []
    const now = Date.now()
    const fiveMinutesAgo = now - 5 * 60 * 1000

    try {
      // Check for recent daily snapshot
      const selectedTeamsRes = await fetch(`${API_BASE}/api/selected-teams`).then(r => r.json()).catch(() => null)
      if (selectedTeamsRes?.teams && selectedTeamsRes.teams.length > 0) {
        const latestTeam = selectedTeamsRes.teams[0]
        const snapshotTime = new Date(latestTeam.saved_at).getTime()
        if (snapshotTime > fiveMinutesAgo) {
          recentTasks.push({
            id: `daily_snapshot_${snapshotTime}`,
            type: 'daily_snapshot',
            title: 'Update Free Hit Squad',
            description: 'Refreshing squad with latest player availability...',
            status: 'completed',
            progress: 100,
            createdAt: snapshotTime - 60000, // Assume it took 1 minute
            completedAt: snapshotTime
          })
        }
      }

      // Check for recent triple captain calculation
      const tcRes = await fetch(`${API_BASE}/api/chips/triple-captain`).then(r => r.json()).catch(() => null)
      if (tcRes && typeof tcRes === 'object') {
        const hasRecs = (tcRes.recommendations && Object.keys(tcRes.recommendations).length > 0) ||
                      (tcRes.gameweeks && Array.isArray(tcRes.gameweeks) && tcRes.gameweeks.length > 0) ||
                      (tcRes.gameweek && typeof tcRes.gameweek === 'object')
        if (hasRecs) {
          // We can't know exactly when it was calculated, so we'll show it as recently completed
          // if recommendations exist (they're only created after calculation completes)
          const estimatedTime = now - 2 * 60 * 1000 // Assume 2 minutes ago
          recentTasks.push({
            id: `triple_captain_${estimatedTime}`,
            type: 'triple_captain',
            title: 'Calculate Triple Captain',
            description: 'Analyzing optimal gameweeks for Triple Captain chip...',
            status: 'completed',
            progress: 100,
            createdAt: estimatedTime - 5 * 60 * 1000, // Assume it took 5 minutes
            completedAt: estimatedTime
          })
        }
      }
    } catch (err) {
      // Silently fail - this is just for showing recent tasks
      console.debug('Error checking backend for recent tasks:', err)
    }

    return recentTasks
  }

  // Load tasks from backend (with localStorage fallback)
  const loadTasksFromStorage = useCallback(async () => {
    try {
      // Try to fetch from backend first
      let tasksToLoad: Task[] = []
      try {
        const res = await fetch(`${API_BASE}/api/tasks?include_old=false`)
        if (res.ok) {
          const data = await res.json()
          if (data.tasks && Array.isArray(data.tasks)) {
            // Convert backend format to frontend format
            tasksToLoad = data.tasks.map((t: any) => ({
              id: t.id,
              type: t.type as TaskType,
              title: t.title,
              description: t.description || '',
              status: t.status as TaskStatus,
              progress: t.progress || 0,
              createdAt: t.createdAt || Date.now(),
              completedAt: t.completedAt,
              error: t.error
            }))
          }
        }
      } catch (err) {
        console.debug('Failed to fetch tasks from backend, falling back to localStorage:', err)
        // Fall back to localStorage
        const savedTasks = localStorage.getItem(TASKS_KEY)
        if (savedTasks) {
          const parsed = JSON.parse(savedTasks) as Task[]
          const now = Date.now()

          // Filter out old completed tasks immediately
          tasksToLoad = parsed.filter(task => {
            // Keep pending and running tasks
            if (task.status === 'pending' || task.status === 'running') {
              return true
            }
            // For completed/failed tasks, keep if less than 5 minutes old
            if (task.completedAt) {
              return (now - task.completedAt) < 5 * 60 * 1000
            }
            return false
          })
        }
      }

      // If still empty (e.g., incognito mode with no backend), check backend for recent operations
      if (tasksToLoad.length === 0) {
        const recentTasks = await checkBackendForRecentTasks()
        tasksToLoad = recentTasks
      }

      // Set tasks immediately (non-blocking) - even if empty
      setTasks(tasksToLoad)

      // Then verify running tasks in the background (don't block render)
      if (tasksToLoad.length > 0) {
        setTimeout(async () => {
          const now = Date.now()
          const verifiedTasks = await Promise.all(
            tasksToLoad.map(async (task) => {
              if (task.status === 'running') {
                // Check if task actually completed
                const completed = await checkTaskCompletion(task)
                if (completed) {
                  const updated = {
                    ...task,
                    status: 'completed' as TaskStatus,
                    progress: 100,
                    completedAt: Date.now()
                  }
                  // Update backend
                  await updateTask(task.id, { status: 'completed', progress: 100 })
                  return updated
                }
                // If task started more than 10 minutes ago, mark as failed
                if (now - task.createdAt > 10 * 60 * 1000) {
                  const updated = {
                    ...task,
                    status: 'failed' as TaskStatus,
                    error: 'Task timed out. It may have completed - please check the results or try again.',
                    completedAt: Date.now()
                  }
                  // Update backend
                  await updateTask(task.id, {
                    status: 'failed',
                    progress: 100,
                    error: updated.error
                  })
                  return updated
                }
                // Otherwise, keep it as running
                return task
              }
              return task
            })
          )

          setTasks(verifiedTasks)

          // Persist verified tasks to localStorage as backup (if not in incognito mode)
          try {
            localStorage.setItem(TASKS_KEY, JSON.stringify(verifiedTasks))
          } catch (err) {
            // Silently fail in incognito mode or if storage is disabled
            console.debug('Could not save tasks to localStorage (may be incognito mode):', err)
          }
        }, 100) // Small delay to not block initial render
      }
    } catch (err) {
      console.error('Failed to load tasks:', err)
      // Ensure tasks state is set even on error
      setTasks([])
    }
  }, [checkTaskCompletion])

  // Load tasks from localStorage on mount (non-blocking)
  useEffect(() => {
    loadTasksFromStorage()
  }, [])

  // Helper to check if tasks are different (for avoiding unnecessary state updates)
  const tasksAreEqual = (a: Task[], b: Task[]): boolean => {
    if (a.length !== b.length) return false
    const aMap = new Map(a.map(t => [t.id, t]))
    for (const taskB of b) {
      const taskA = aMap.get(taskB.id)
      if (!taskA) return false
      // Compare relevant fields that affect UI
      if (taskA.status !== taskB.status ||
          taskA.progress !== taskB.progress ||
          taskA.completedAt !== taskB.completedAt ||
          taskA.error !== taskB.error) {
        return false
      }
    }
    return true
  }

  // Reusable function to poll and update tasks from backend
  const pollTasksFromBackend = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/tasks?include_old=false`)
      if (res.ok) {
        const data = await res.json()
        if (data.tasks && Array.isArray(data.tasks)) {
          // Convert backend format to frontend format
          const backendTasks: Task[] = data.tasks.map((t: any) => ({
            id: t.id,
            type: t.type as TaskType,
            title: t.title,
            description: t.description || '',
            status: t.status as TaskStatus,
            progress: t.progress || 0,
            createdAt: t.createdAt || Date.now(),
            completedAt: t.completedAt,
            error: t.error
          }))

          // Update tasks state with latest status from backend (only if changed)
          setTasks(prevTasks => {
            // Create a map of backend tasks by id for quick lookup
            const backendTaskMap = new Map<string, Task>(backendTasks.map(t => [t.id, t] as [string, Task]))

            // Update existing tasks with backend status, or add new tasks
            const updatedTasks = prevTasks.map(task => {
              const backendTask = backendTaskMap.get(task.id)
              if (backendTask) {
                // Update with backend status
                return backendTask
              }
              // Keep existing task if not in backend (might be local-only)
              return task
            })

            // Add any new tasks from backend that we don't have
            backendTasks.forEach(backendTask => {
              if (!updatedTasks.find(t => t.id === backendTask.id)) {
                updatedTasks.push(backendTask)
              }
            })

            // Only update state if something actually changed
            if (tasksAreEqual(prevTasks, updatedTasks)) {
              return prevTasks // Return same reference to avoid re-render
            }

            return updatedTasks
          })
        }
      }
    } catch (err) {
      // Silently fail - polling errors shouldn't break the UI
      console.debug('Error polling task status:', err)
    }
  }, [])

  // Persist tasks to localStorage (debounced to avoid excessive writes)
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      try {
        localStorage.setItem(TASKS_KEY, JSON.stringify(tasks))
      } catch (err) {
        console.error('Failed to save tasks to localStorage:', err)
      }
    }, 1000) // Debounce: only write 1 second after last change

    return () => clearTimeout(timeoutId)
  }, [tasks])

  // Handle immediate poll when task is created
  useEffect(() => {
    if (needsImmediatePollRef.current) {
      needsImmediatePollRef.current = false
      // Small delay to allow backend to process the new task
      const timeoutId = setTimeout(() => {
        pollTasksFromBackend()
      }, 500)
      return () => clearTimeout(timeoutId)
    }
  }, [tasks.length, pollTasksFromBackend]) // Trigger when task count changes

  // Poll for task status updates - always poll to detect new tasks
  useEffect(() => {
    const hasActiveTasks = tasks.some(task => task.status === 'running' || task.status === 'pending')

    // Use shorter interval (3s) when there are active tasks, longer (10s) when idle
    // We still poll when idle to detect new tasks that might be created in the backend
    const pollInterval = hasActiveTasks ? 3000 : 10000

    // Poll immediately, then set up interval
    pollTasksFromBackend()
    const pollIntervalId = setInterval(pollTasksFromBackend, pollInterval)

    return () => {
      clearInterval(pollIntervalId)
    }
  }, [tasks, pollTasksFromBackend])

  // Detect task completion and show notifications
  useEffect(() => {
    // Request notification permission on first load
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {
        // Silently fail if permission is denied
      })
    }

    // Compare current tasks with previous tasks to detect completion
    const previousTasks = previousTasksRef.current
    if (previousTasks.length > 0) {
      tasks.forEach(currentTask => {
        const previousTask = previousTasks.find(t => t.id === currentTask.id)

        // Detect transition from running/pending to completed
        if (previousTask &&
            (previousTask.status === 'running' || previousTask.status === 'pending') &&
            currentTask.status === 'completed') {

          // Show browser notification
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('Task Completed', {
              body: `${currentTask.title} has finished successfully.`,
              icon: '/favicon.ico', // Optional: add favicon if available
              tag: `task-${currentTask.id}`, // Prevent duplicate notifications
            })
          }
        }
      })
    }

    // Update previous tasks ref
    previousTasksRef.current = tasks
  }, [tasks])

  // Task management helpers
  const createTask = async (type: TaskType, title: string, description: string, showModal: boolean = true): Promise<string> => {
    const taskId = `${type}_${Date.now()}`
    const newTask: Task = {
      id: taskId,
      type,
      title,
      description,
      status: 'pending',
      progress: 0,
      createdAt: Date.now()
    }

    // Save to backend first
    try {
      await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          task_type: type,
          title,
          description,
          status: 'pending',
          progress: 0
        })
      })
    } catch (err) {
      console.error('Failed to save task to backend:', err)
      // Continue anyway - will fall back to localStorage
    }

    setTasks(prev => {
      const updated = [...prev, newTask]
      return updated
    })

    // Mark that we need immediate poll (will be handled by polling effect)
    needsImmediatePollRef.current = true

    // Poll immediately and then again after a short delay to catch status updates
    // (backend might update status from 'pending' to 'running' asynchronously)
    pollTasksFromBackend() // Immediate poll
    setTimeout(() => {
      pollTasksFromBackend() // Poll again after 500ms
    }, 500)
    setTimeout(() => {
      pollTasksFromBackend() // Poll again after 2s to be sure
    }, 2000)

    // Show modal if requested
    if (showModal) {
      setTaskStartedModal({ taskId, title })
    }

    return taskId
  }

  const updateTask = async (taskId: string, updates: Partial<Task>) => {
    // Update backend first
    try {
      await fetch(`${API_BASE}/api/tasks/${taskId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          status: updates.status,
          progress: updates.progress,
          error: updates.error
        })
      })
    } catch (err) {
      console.error('Failed to update task on backend:', err)
      // Continue anyway - will fall back to localStorage
    }

    setTasks(prev => {
      const updated = prev.map(task =>
        task.id === taskId ? { ...task, ...updates } : task
      )
      // Also persist to localStorage as backup
      try {
        localStorage.setItem(TASKS_KEY, JSON.stringify(updated))
      } catch (err) {
        console.error('Failed to save tasks to localStorage:', err)
      }
      return updated
    })
  }

  const completeTask = async (taskId: string, success: boolean, error?: string) => {
    // Update backend first
    try {
      await fetch(`${API_BASE}/api/tasks/${taskId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          status: success ? 'completed' : 'failed',
          progress: 100,
          error: error
        })
      })
    } catch (err) {
      console.error('Failed to update task on backend:', err)
      // Continue anyway
    }

    // Get task info before updating (to avoid stale closure)
    setTasks(prev => {
      const task = prev.find(t => t.id === taskId)
      if (!task) return prev

      // Update task status
      const updatedTasks = prev.map(t =>
        t.id === taskId
          ? { ...t, status: (success ? 'completed' : 'failed') as TaskStatus, progress: 100, completedAt: Date.now(), error }
          : t
      )

      // Show notification
      addNotification(
        success ? 'success' : 'error',
        task.title,
        success
          ? task.type === 'daily_snapshot'
            ? 'Free hit squad updated successfully!'
            : 'Triple Captain calculation completed!'
          : error || 'Task failed'
      )

      // Auto-remove completed tasks after 5 minutes
      setTimeout(async () => {
        // Delete from backend
        try {
          await fetch(`${API_BASE}/api/tasks/${taskId}`, { method: 'DELETE' })
        } catch (err) {
          console.debug('Failed to delete task from backend:', err)
        }

        setTasks(current => {
          const filtered = current.filter(t => t.id !== taskId)
          // Also persist to localStorage as backup
          try {
            localStorage.setItem(TASKS_KEY, JSON.stringify(filtered))
          } catch (err) {
            console.error('Failed to save tasks:', err)
          }
          return filtered
        })
      }, 5 * 60 * 1000)

      // Persist updated tasks to localStorage as backup
      try {
        localStorage.setItem(TASKS_KEY, JSON.stringify(updatedTasks))
      } catch (err) {
        console.error('Failed to save tasks:', err)
      }

      return updatedTasks
    })
  }

  const addNotification = (type: 'success' | 'error', title: string, message: string) => {
    const notificationId = `notif_${Date.now()}`
    setNotifications(prev => [...prev, { id: notificationId, type, title, message, timestamp: Date.now() }])

    // Auto-remove after 5 seconds
    setTimeout(() => {
      setNotifications(prev => prev.filter(n => n.id !== notificationId))
    }, 5000)
  }

  // Helper to check if a task type is currently running
  const isTaskRunning = (taskType: TaskType): boolean => {
    return tasks.some(task => task.type === taskType && task.status === 'running')
  }

  return {
    tasks,
    setTasks,
    notifications,
    setNotifications,
    taskStartedModal,
    setTaskStartedModal,
    loadTasksFromStorage,
    createTask,
    updateTask,
    completeTask,
    addNotification,
    isTaskRunning,
  }
}
