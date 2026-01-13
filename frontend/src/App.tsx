import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { 
  Users, TrendingUp, RefreshCw, Zap, Award, 
  ChevronRight, ChevronDown, ChevronUp, Star, Target, Flame, AlertTriangle, Plane,
  ArrowRightLeft, Search, Plus, X, Trash2, Trophy, Home, Brain, Crown, CheckCircle2, Clock, AlertCircle, Loader2
} from 'lucide-react'

// Type imports from modular types directory
import type { 
  Player, 
  SuggestedSquad, 
  SquadPlayer, 
  SelectedTeam,
  TransferSuggestion, 
  Task, 
  TaskStatus, 
  TaskType,
  GameWeekInfo, 
  SavedFplTeam 
} from './types'

// Component imports
import { FPLLogo } from './components'
import { HomeTab, DifferentialsTab, PicksTab, TasksTab, TripleCaptainTab, SelectedTeamsTab, TransfersTab, WildcardTab } from './tabs'

// In production (GitHub Pages) set this to your hosted backend, e.g. https://api.fplai.nl
// In local dev it defaults to http://localhost:8001
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8001'

function App() {
  const [loading, setLoading] = useState(true)
  const [squad, setSquad] = useState<SuggestedSquad | null>(null)
  const [squadHeuristic, setSquadHeuristic] = useState<SuggestedSquad | null>(null)
  const [squadForm, setSquadForm] = useState<SuggestedSquad | null>(null)
  const [squadFixture, setSquadFixture] = useState<SuggestedSquad | null>(null)
  const [squadLSTM, setSquadLSTM] = useState<SuggestedSquad | null>(null)
  const [statisticsMethod, setStatisticsMethod] = useState<'combined' | 'heuristic' | 'form' | 'fixture'>('combined')
  const [topPicks, setTopPicks] = useState<Record<string, Player[]>>({})
  const [differentials, setDifferentials] = useState<Player[]>([])
  const [tripleCaptainRecs, setTripleCaptainRecs] = useState<Record<number, any>>({})
  const [loadingTripleCaptain, setLoadingTripleCaptain] = useState(false)
  const [selectedTcGameweekTab, setSelectedTcGameweekTab] = useState<number | null>(null)
  const [tcPollingInterval, setTcPollingInterval] = useState<ReturnType<typeof setInterval> | null>(null)
  
  // AbortController refs for cleanup
  const tcAbortControllerRef = useRef<AbortController | null>(null)
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  // Initialize activeTab from URL hash (e.g., #transfers -> 'transfers')
  const [activeTab, setActiveTab] = useState(() => {
    const hash = window.location.hash.slice(1) // Remove the '#'
    const validTabs = ['home', 'picks', 'differentials', 'transfers', 'wildcard', 'triple_captain', 'selected_teams', 'tasks']
    return validTabs.includes(hash) ? hash : 'home'
  })
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [countdown, setCountdown] = useState<{ days: number; hours: number; minutes: number; seconds: number } | null>(null)
  // Transfer tab state
  const [mySquad, setMySquad] = useState<SquadPlayer[]>([])
  const [bank, setBank] = useState(0)
  const [bankInput, setBankInput] = useState('0')
  const [freeTransfers, setFreeTransfers] = useState(1)
  // Track slot positions for each player to preserve positions when removing
  const [playerSlotPositions, setPlayerSlotPositions] = useState<Map<number, { position: string; slotIndex: number }>>(new Map())
  const [transferSuggestions, setTransferSuggestions] = useState<TransferSuggestion[]>([])
  const [squadAnalysis, setSquadAnalysis] = useState<any[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Player[]>([])
  const [searchPosition, setSearchPosition] = useState<string>('')
  const [transferLoading, setTransferLoading] = useState(false)

  // Wildcard state (for WildcardTab - trajectory optimization)
  const [wildcardTrajectory, setWildcardTrajectory] = useState<any>(null)
  const [loadingWildcard, setLoadingWildcard] = useState(false)
  
  // Wildcard plan state (for TransfersTab - 4+ transfer suggestions)
  const [wildcardPlan, setWildcardPlan] = useState<any>(null)
  const [wildcardLoading, setWildcardLoading] = useState(false)
  
  // Expanded groups for transfer suggestions
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  
  // Ref for scrolling to results after generation
  const resultsSectionRef = useRef<HTMLDivElement>(null)
  
  // Ref for scrolling to squad section after import
  const squadSectionRef = useRef<HTMLDivElement>(null)
  
  // Memoized grouped transfer suggestions
  const groupedTransferSuggestions = useMemo(() => {
    if (transferSuggestions.length === 0) return null
    
    // Separate hold suggestions from transfer suggestions
    const holdSuggestions = transferSuggestions.filter((s: any) => s.type === 'hold')
    const transferOnly = transferSuggestions.filter((s: any) => s.type !== 'hold')
    
    // Group transfers by transfer-out player
    const groupedByOut: Record<string, TransferSuggestion[]> = {}
    transferOnly.forEach((suggestion) => {
      const outPlayerId = suggestion.out.id || suggestion.out.name
      if (!groupedByOut[outPlayerId]) {
        groupedByOut[outPlayerId] = []
      }
      groupedByOut[outPlayerId].push(suggestion)
    })
    
    // Sort groups by best priority_score in each group, then take top N groups (N = free transfers)
    const sortedGroups = Object.entries(groupedByOut)
      .map(([outPlayerId, suggestions]) => ({
        outPlayer: suggestions[0].out,
        suggestions: suggestions
          .sort((a, b) => (b.priority_score || 0) - (a.priority_score || 0))
          .slice(0, 3) // Top 3 transfer-in options per group
      }))
      .sort((a, b) => {
        const aBest = a.suggestions[0]?.priority_score || 0
        const bBest = b.suggestions[0]?.priority_score || 0
        return bBest - aBest
      })
      .slice(0, Math.max(1, Number.isFinite(freeTransfers) ? freeTransfers : 1)) // Limit to number of free transfers (each group = 1 transfer)
    
    return { holdSuggestions, sortedGroups }
  }, [transferSuggestions, freeTransfers])

  // FPL team import
  const [fplTeamId, setFplTeamId] = useState<string>('')
  const [importingFplTeam, setImportingFplTeam] = useState(false)
  
  // Saved FPL team IDs (types imported from ./types)
  const [savedFplTeams, setSavedFplTeams] = useState<SavedFplTeam[]>([])
  const [selectedSavedFplTeamId, setSelectedSavedFplTeamId] = useState<number | ''>('')

  // Selected teams (suggested squads for each gameweek) - fetched from API
  const [selectedTeams, setSelectedTeams] = useState<Record<number, SelectedTeam>>({})
  const [loadingSelectedTeams, setLoadingSelectedTeams] = useState(false)
  const [updatingSnapshot, setUpdatingSnapshot] = useState(false)
  const [snapshotUpdateMessage, setSnapshotUpdateMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null)
  const [selectedGameweekTab, setSelectedGameweekTab] = useState<number | null>(null)

  // Task management
  const [tasks, setTasks] = useState<Task[]>([])
  const [notifications, setNotifications] = useState<Array<{ id: string; type: 'success' | 'error'; title: string; message: string; timestamp: number }>>([])
  const [taskStartedModal, setTaskStartedModal] = useState<{ taskId: string; title: string } | null>(null)
  
  // Ref to track if we need immediate poll (after task creation)
  const needsImmediatePollRef = useRef(false)

  const DRAFT_KEY = 'fpl_squad_draft_v1' // Still used for local draft auto-save
  const TASKS_KEY = 'fpl_tasks_v1' // Key for persisting tasks
  const FPL_TEAMS_KEY = 'fpl_imported_teams_v1' // Store imported FPL team IDs

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
          const backendTasks = data.tasks.map((t: any) => ({
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
            const backendTaskMap = new Map(backendTasks.map(t => [t.id, t]))
            
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

  useEffect(() => {
    loadInitial()
  }, [])

  // Sync URL hash when activeTab changes
  useEffect(() => {
    const currentHash = window.location.hash.slice(1)
    const expectedHash = activeTab === 'home' ? '' : activeTab
    if (currentHash !== expectedHash) {
      const newUrl = activeTab === 'home' ? window.location.pathname : `#${activeTab}`
      window.history.pushState(null, '', newUrl)
    }
  }, [activeTab])

  // Listen for browser back/forward navigation
  useEffect(() => {
    const handleNavigation = () => {
      const hash = window.location.hash.slice(1)
      const validTabs = ['home', 'picks', 'differentials', 'transfers', 'wildcard', 'triple_captain', 'selected_teams', 'tasks']
      setActiveTab(validTabs.includes(hash) ? hash : 'home')
    }
    window.addEventListener('popstate', handleNavigation)
    window.addEventListener('hashchange', handleNavigation)
    return () => {
      window.removeEventListener('popstate', handleNavigation)
      window.removeEventListener('hashchange', handleNavigation)
    }
  }, [])

  // Countdown timer for gameweek deadline
  useEffect(() => {
    if (!gameweek?.next?.deadline) {
      setCountdown(null)
      return
    }

    const updateCountdown = () => {
      if (!gameweek?.next?.deadline) return
      const deadline = new Date(gameweek.next.deadline).getTime()
      const now = new Date().getTime()
      const diff = deadline - now

      if (diff <= 0) {
        setCountdown({ days: 0, hours: 0, minutes: 0, seconds: 0 })
        return
      }

      const days = Math.floor(diff / (1000 * 60 * 60 * 24))
      const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))
      const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))
      const seconds = Math.floor((diff % (1000 * 60)) / 1000)

      setCountdown({ days, hours, minutes, seconds })
    }

    // Update immediately
    updateCountdown()

    // Update every second
    const interval = setInterval(updateCountdown, 1000)

    return () => clearInterval(interval)
  }, [gameweek?.next?.deadline])

  // Load saved FPL team IDs from database
  const loadSavedFplTeams = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/fpl-teams`)
      if (!res.ok) {
        console.error(`Failed to load saved FPL teams: HTTP ${res.status}`)
        setSavedFplTeams([])
        return
      }
      const data = await res.json()
      if (data.teams && Array.isArray(data.teams)) {
        // Map API response to frontend format
        const mapped: SavedFplTeam[] = data.teams.map((t: any) => ({
          teamId: t.teamId,
          teamName: t.teamName,
          lastImported: t.lastImported ? new Date(t.lastImported).getTime() : Date.now()
        }))
        setSavedFplTeams(mapped)
        console.log(`Loaded ${mapped.length} saved FPL team(s) from database`)
      } else {
        console.warn('Unexpected response format from fpl-teams endpoint:', data)
        setSavedFplTeams([])
      }
    } catch (err) {
      console.error('Failed to load saved FPL teams:', err)
      setSavedFplTeams([])
    }
  }, [])

  // Load FPL teams and draft on mount
  useEffect(() => {
    loadSavedFplTeams()

    // Load draft squad from localStorage (still local, not synced)
    try {
      const rawDraft = localStorage.getItem(DRAFT_KEY)
      if (rawDraft) {
        const d = JSON.parse(rawDraft)
        if (d && Array.isArray(d.squad)) {
          setMySquad(d.squad)
          if (typeof d.bank === 'number') {
            setBank(d.bank)
            setBankInput(String(d.bank))
          }
          if (typeof d.freeTransfers === 'number') setFreeTransfers(d.freeTransfers)
        }
      }
    } catch {}
  }, [loadSavedFplTeams])

  // Load selected teams from API
  const loadSelectedTeams = async () => {
    setLoadingSelectedTeams(true)
    try {
      const res = await fetch(`${API_BASE}/api/selected-teams`).then(r => r.json())
      const teams: Record<number, SelectedTeam> = {}
      if (res.teams && Array.isArray(res.teams)) {
        res.teams.forEach((team: SelectedTeam) => {
          teams[team.gameweek] = team
        })
      }
      setSelectedTeams(teams)
      // Set first gameweek as selected tab if none selected
      if (!selectedGameweekTab && res.teams && res.teams.length > 0) {
        setSelectedGameweekTab(res.teams[0].gameweek)
      }
    } catch (err) {
      console.error('Failed to load selected teams:', err)
    } finally {
      setLoadingSelectedTeams(false)
    }
  }

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
          ? { ...t, status: success ? 'completed' : 'failed', progress: 100, completedAt: Date.now(), error }
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

  /**
   * Generic helper for creating background refresh tasks
   * 
   * Usage example for adding a new refresh button:
   * 
   * const refreshPicks = () => {
   *   if (isTaskRunning('refresh_picks')) return
   *   
   *   createRefreshTask(
   *     'refresh_picks',                    // Task type (add to TaskType)
   *     'Refresh Top Picks',                // Task title
   *     'Updating player predictions...',    // Task description
   *     '/api/picks/refresh',                // API endpoint
   *     'POST',                              // HTTP method
   *     {
   *       onSuccess: () => {
   *         // Reload data
   *         loadPicks()
   *       },
   *       onError: (error) => {
   *         // Handle error
   *         console.error(error)
   *       },
   *       // Optional: custom progress updater
   *       progressUpdater: (taskId, pollCount, maxPolls) => {
   *         const progress = Math.min(30 + (pollCount / maxPolls) * 60, 90)
   *         updateTask(taskId, { progress })
   *       },
   *       // Optional: completion checker (for polling)
   *       completionChecker: (data, startTime) => {
   *         return data.updated && new Date(data.updated).getTime() > startTime
   *       },
   *       checkEndpoint: '/api/picks',      // Endpoint to poll for completion
   *       maxPolls: 30,                      // Max polling attempts (default 30)
   *       pollInterval: 10000               // Poll interval in ms (default 10000)
   *     }
   *   )
   * }
   * 
   * Then in your button:
   * <button 
   *   onClick={refreshPicks}
   *   disabled={isTaskRunning('refresh_picks')}
   * >
   *   <RefreshCw className={isTaskRunning('refresh_picks') ? 'animate-spin' : ''} />
   *   {isTaskRunning('refresh_picks') ? 'Refreshing...' : 'Refresh'}
   * </button>
   */
  const createRefreshTask = async (
    taskType: TaskType,
    title: string,
    description: string,
    apiEndpoint: string,
    method: 'GET' | 'POST' = 'POST',
    options?: {
      onSuccess?: (data: any) => void
      onError?: (error: string) => void
      progressUpdater?: (taskId: string, pollCount: number, maxPolls: number) => void
      completionChecker?: (data: any, taskStartTime: number) => boolean
      checkEndpoint?: string // Endpoint to poll for completion
      maxPolls?: number // Max polling attempts (default 30 = 5 minutes)
      pollInterval?: number // Polling interval in ms (default 10000 = 10 seconds)
    }
  ): Promise<string> => {
    const taskId = await createTask(taskType, title, description)
    const taskStartTime = Date.now()
    const {
      onSuccess,
      onError,
      progressUpdater,
      completionChecker,
      checkEndpoint,
      maxPolls = 30,
      pollInterval = 10000
    } = options || {}
    
    // Start the task
    updateTask(taskId, { status: 'running', progress: 10 })
    
    // Make API call
    fetch(`${API_BASE}${apiEndpoint}`, { method })
      .then(r => r.json())
      .then(res => {
        if (res.success) {
          updateTask(taskId, { progress: 30 })
          
          // If there's a completion checker, use polling
          if (completionChecker && checkEndpoint) {
            let pollCount = 0
            
            const pollingInterval = setInterval(async () => {
              pollCount++
              
              // Update progress
              if (progressUpdater) {
                progressUpdater(taskId, pollCount, maxPolls)
              } else {
                // More realistic progress: 30% -> 90% with smooth curve and variation
                const ratio = pollCount / maxPolls
                // Use ease-in-out curve for more natural progression
                const easedRatio = ratio < 0.5 
                  ? 2 * ratio * ratio 
                  : 1 - Math.pow(-2 * ratio + 2, 2) / 2
                // Base progress from 30% to 90%
                const baseProgress = 30 + easedRatio * 60
                // Add small random variation (±2%) to make it feel more dynamic
                const variation = (Math.random() - 0.5) * 4
                // Add slight acceleration based on time elapsed (makes it feel more realistic)
                const timeElapsed = (Date.now() - taskStartTime) / 1000 // seconds
                const timeBoost = Math.min(timeElapsed * 0.5, 3) // small boost up to 3%
                const progress = Math.min(Math.max(baseProgress + variation + timeBoost, 30), 92)
                updateTask(taskId, { progress })
              }
              
              // Check for completion
              try {
                const checkData = await fetch(`${API_BASE}${checkEndpoint}`)
                  .then(r => r.json())
                  .catch(() => null)
                
                // Handle async completion checker
                const isComplete = await Promise.resolve(completionChecker(checkData, taskStartTime))
                
                if (checkData && isComplete) {
                  clearInterval(pollingInterval)
                  updateTask(taskId, { progress: 100 })
                  completeTask(taskId, true)
                  if (onSuccess) onSuccess(checkData)
                  return
                }
              } catch (err) {
                // Continue polling on error
                console.debug('Polling error (continuing):', err)
              }
              
              // Check if we reached max polls
              if (pollCount >= maxPolls) {
                clearInterval(pollingInterval)
                const errorMsg = 'Task is taking longer than expected. Please refresh the page in a few minutes.'
                completeTask(taskId, false, errorMsg)
                if (onError) onError(errorMsg)
              }
            }, pollInterval)
          } else {
            // No polling needed, complete immediately
            updateTask(taskId, { progress: 100 })
            completeTask(taskId, true)
            if (onSuccess) onSuccess(res)
          }
        } else {
          const errorMsg = res.message || 'Task failed'
          completeTask(taskId, false, errorMsg)
          if (onError) onError(errorMsg)
        }
      })
      .catch(err => {
        console.error(`Task ${taskType} failed:`, err)
        const errorMsg = 'Failed to start task. Please try again.'
        completeTask(taskId, false, errorMsg)
        if (onError) onError(errorMsg)
      })
    
    return taskId
  }

  // Update daily snapshot manually
  const updateDailySnapshot = async () => {
    // Check if already running
    if (isTaskRunning('daily_snapshot')) {
      return
    }
    
    setUpdatingSnapshot(true)
    setSnapshotUpdateMessage(null)
    
    await createRefreshTask(
      'daily_snapshot',
      'Update Free Hit Squad',
      'Refreshing squad with latest player availability...',
      '/api/daily-snapshot/update',
      'POST',
      {
        onSuccess: () => {
          setUpdatingSnapshot(false)
          setSnapshotUpdateMessage({ 
            type: 'success', 
            text: 'Free hit squad updated successfully!' 
          })
          setTimeout(() => setSnapshotUpdateMessage(null), 5000)
          loadSelectedTeams()
        },
        onError: (error) => {
          setUpdatingSnapshot(false)
          setSnapshotUpdateMessage({ type: 'error', text: error })
          setTimeout(() => setSnapshotUpdateMessage(null), 5000)
        },
        progressUpdater: (taskId, pollCount, maxPolls) => {
          // Custom progress updater: 30% -> 90% over polling period
          const progress = Math.min(30 + (pollCount / maxPolls) * 60, 90)
          updateTask(taskId, { progress })
        },
        completionChecker: (data, startTime) => {
          // Completion checker: check if new snapshot exists
          if (data.teams && data.teams.length > 0) {
            const latestTeam = data.teams[0]
            const snapshotTime = new Date(latestTeam.saved_at).getTime()
            return snapshotTime > startTime
          }
          return false
        },
        checkEndpoint: '/api/selected-teams',
        maxPolls: 30, // 5 minutes
        pollInterval: 10000 // 10 seconds
      }
    )
  }

  // Load selected teams when the tab is active
  useEffect(() => {
    if (activeTab === 'selected_teams') {
      loadSelectedTeams()
    }
  }, [activeTab])

  // Auto-save draft whenever squad/bank/freeTransfers change
  useEffect(() => {
    try {
      const payload = { squad: mySquad, bank, freeTransfers, updatedAt: Date.now() }
      localStorage.setItem(DRAFT_KEY, JSON.stringify(payload))
    } catch {}
  }, [mySquad, bank, freeTransfers])

  // Import squad from saved FPL team ID (always fetches latest from FPL)
  const importFromSavedFplTeam = useCallback(async (teamId: number) => {
    setImportingFplTeam(true)
    try {
      // Always fetch latest team data from FPL API
      const res = await fetch(`${API_BASE}/api/fpl-teams/import/${teamId}`)
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Failed to import FPL team' }))
        throw new Error(error.detail || 'Failed to import FPL team')
      }
      
      const data = await res.json()
      const squad = data.squad || []
      const importedBank = data.bank || 0
      const teamName = data.team_name || `FPL Team ${teamId}`
      
      // Backend automatically saves/updates FPL team in database, so reload the list
      await loadSavedFplTeams()
      
      // Load the squad into the UI
      setMySquad(squad)
      setBank(importedBank)
      setBankInput(String(importedBank))
      
      // Reset view when importing a new squad
      setWildcardPlan(null)
      setTransferSuggestions([])
      setSquadAnalysis([])
      
      // Reset dropdown selection so it can be used again
      setSelectedSavedFplTeamId('')
      
      alert(`Successfully imported ${teamName}!`)
      
      // Scroll to squad section after import
      setTimeout(() => {
        if (squadSectionRef.current) {
          squadSectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }, 100)
    } catch (err: any) {
      console.error('Failed to import FPL team:', err)
      alert(err.message || 'Failed to import FPL team. Please check the Team ID and try again.')
    } finally {
      setImportingFplTeam(false)
    }
  }, [loadSavedFplTeams])

  const importFplTeam = useCallback(async () => {
    const teamId = parseInt(fplTeamId.trim())
    if (!teamId || isNaN(teamId) || teamId <= 0) {
      alert('Please enter a valid FPL Team ID')
      return
    }
    
    setImportingFplTeam(true)
    try {
      // Import team from FPL (backend automatically saves to database)
      const res = await fetch(`${API_BASE}/api/fpl-teams/import/${teamId}`)
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Failed to import FPL team' }))
        throw new Error(error.detail || 'Failed to import FPL team')
      }
      
      const data = await res.json()
      const squad = data.squad || []
      const importedBank = data.bank || 0
      const teamName = data.team_name || `FPL Team ${teamId}`
      
      // Backend automatically saves/updates FPL team in database, so reload the list
      await loadSavedFplTeams()
      
      // Load the squad into the UI
      setMySquad(squad)
      setBank(importedBank)
      setBankInput(String(importedBank))
      
      // Reset view when importing a new squad
      setWildcardPlan(null)
      setTransferSuggestions([])
      setSquadAnalysis([])
      
      // Clear the input
      setFplTeamId('')
      setSelectedSavedFplTeamId('')
      
      alert(`Successfully imported ${teamName}!`)
      
      // Scroll to squad section after import
      setTimeout(() => {
        if (squadSectionRef.current) {
          squadSectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }, 100)
    } catch (err: any) {
      console.error('Failed to import FPL team:', err)
      alert(err.message || 'Failed to import FPL team. Please check the Team ID and try again.')
    } finally {
      setImportingFplTeam(false)
    }
  }, [fplTeamId, loadSavedFplTeams])

  const loadInitial = async () => {
    // Only load lightweight header data on boot (keeps Quick Transfers instant).
    setLoading(true)
    setError(null)
    try {
      const gwRes = await fetch(`${API_BASE}/api/gameweek`).then(r => r.json())
      setGameweek(gwRes)
    } catch (err: any) {
      setError(err.message || 'Failed to load data')
      console.error('Load error:', err)
    } finally {
      setLoading(false)
    }
  }

  const ensurePicksLoaded = async () => {
    if (Object.keys(topPicks).length > 0) return
    try {
      const topsRes = await fetch(`${API_BASE}/api/top-picks`).then(r => r.json())
      setTopPicks(topsRes)
    } catch (err) {
      console.error('Top picks load error:', err)
    }
  }

  const ensureDifferentialsLoaded = async () => {
    if (differentials.length > 0) return
    try {
      const diffsRes = await fetch(`${API_BASE}/api/differentials`).then(r => r.json())
      setDifferentials(diffsRes.differentials || [])
    } catch (err) {
      console.error('Differentials load error:', err)
    }
  }

  const loadWildcardTrajectory = async () => {
    // Always load when called (don't skip if already loaded - allows refresh)
    setLoadingWildcard(true)
    try {
      const response = await fetch(`${API_BASE}/api/chips/wildcard-trajectory/latest`)
      if (!response.ok) {
        if (response.status === 404) {
          // No trajectory calculated yet - try to load default suggested squad
          try {
            const squadResponse = await fetch(`${API_BASE}/api/suggested-squad?budget=100&method=combined`)
            if (squadResponse.ok) {
              const squadData = await squadResponse.json()
              if (squadData.squad) {
                // Convert suggested squad to wildcard trajectory format
                const allPlayers = [...squadData.squad.starting_xi, ...squadData.squad.bench]
                const trajectoryPlayers = allPlayers.map((p: any) => ({
                  id: p.id,
                  name: p.name,
                  team: p.team || '???',
                  team_id: p.team_id || 0,
                  position: p.position,
                  position_id: p.position_id || 1,
                  price: p.price,
                  form: p.form || 0,
                  total_points: p.total_points || 0,
                  ownership: p.ownership || 0,
                  predicted_points: p.predicted || p.predicted_points || 0,
                  avg_fdr: p.avg_fdr || p.difficulty || 3.0,
                  fixture_swing: 0,
                  gameweek_predictions: {}
                }))
                
                const captain = trajectoryPlayers.find((p: any) => p.id === squadData.squad.captain.id) || trajectoryPlayers[0]
                const viceCaptain = trajectoryPlayers.find((p: any) => p.id === squadData.squad.vice_captain.id) || trajectoryPlayers[1] || trajectoryPlayers[0]
                
                const gameweekBreakdown = {
                  gameweek: squadData.squad.gameweek,
                  formation: squadData.squad.formation,
                  predicted_points: squadData.squad.predicted_points,
                  starting_xi: squadData.squad.starting_xi.map((p: any) => ({
                    id: p.id,
                    name: p.name,
                    team: p.team || '???',
                    position: p.position,
                    predicted: p.predicted || p.predicted_points || 0,
                    opponent: p.opponent || '???',
                    fdr: p.fdr || p.difficulty || 3,
                    is_home: p.is_home || false
                  }))
                }
                
                const defaultTrajectory = {
                  squad: trajectoryPlayers,
                  starting_xi: trajectoryPlayers.slice(0, 11),
                  bench: trajectoryPlayers.slice(11),
                  captain,
                  vice_captain: viceCaptain,
                  formation: squadData.squad.formation,
                  gameweek_predictions: {
                    [squadData.squad.gameweek]: gameweekBreakdown
                  },
                  total_predicted_points: squadData.squad.predicted_points,
                  avg_weekly_points: squadData.squad.predicted_points,
                  total_cost: squadData.squad.total_cost,
                  remaining_budget: squadData.squad.remaining_budget,
                  horizon: 1,
                  fixture_blocks: [],
                  rationale: 'Default suggested squad. Generate a wildcard trajectory for optimized 8-gameweek planning.'
                }
                setWildcardTrajectory(defaultTrajectory)
                return
              }
            }
          } catch (squadErr) {
            console.debug('Could not load default suggested squad:', squadErr)
          }
          // No trajectory and no default squad - that's okay
          setWildcardTrajectory(null)
          return
        }
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      if (data && data.squad && data.squad.length > 0) {
        setWildcardTrajectory(data)
      } else {
        setWildcardTrajectory(null)
      }
    } catch (err) {
      console.error('Failed to load wildcard trajectory:', err)
      setWildcardTrajectory(null)
    } finally {
      setLoadingWildcard(false)
    }
  }

  const handleWildcardGenerate = async (budget: number, horizon: number) => {
    // After generation completes, reload from database
    await loadWildcardTrajectory()
  }

  const ensureTripleCaptainLoaded = async () => {
    // Cancel any existing load
    if (tcAbortControllerRef.current) {
      tcAbortControllerRef.current.abort()
    }
    
    // Don't reload if we already have data and we're not forcing a refresh
    if (Object.keys(tripleCaptainRecs).length > 0 || loadingTripleCaptain) return
    
    // Create new AbortController
    const abortController = new AbortController()
    tcAbortControllerRef.current = abortController
    
    setLoadingTripleCaptain(true)
    try {
      const response = await fetch(`${API_BASE}/api/chips/triple-captain?top_n=20`, {
        signal: abortController.signal,
      })
      
      // Check if aborted
      if (abortController.signal.aborted) {
        return
      }
      
      if (!response.ok) {
        if (response.status === 404) {
          // No recommendations calculated yet
          setTripleCaptainRecs({})
          return
        }
        throw new Error(`HTTP ${response.status}`)
      }
      const tcRes = await response.json()
      
      // Check again if aborted after async operation
      if (abortController.signal.aborted) {
        return
      }
      
      if (tcRes.recommendations_by_gameweek) {
        // Multiple gameweeks - store by gameweek
        setTripleCaptainRecs(tcRes.recommendations_by_gameweek)
        // Set first gameweek as selected tab if none selected
        const gameweeks = Object.keys(tcRes.recommendations_by_gameweek).map(Number).sort((a, b) => b - a)
        if (gameweeks.length > 0 && !selectedTcGameweekTab) {
          setSelectedTcGameweekTab(gameweeks[0])
        }
      } else if (tcRes.recommendations) {
        // Single gameweek response (backward compatibility)
        const gw = tcRes.gameweek || gameweek?.next?.id
        if (gw) {
          setTripleCaptainRecs({ [gw]: tcRes })
          if (!selectedTcGameweekTab) {
            setSelectedTcGameweekTab(gw)
          }
        }
      }
    } catch (err: any) {
      // Don't show error if it was aborted (user navigated away)
      if (err.name === 'AbortError' || abortController.signal.aborted) {
        return
      }
      console.error('Triple Captain load error:', err)
      setTripleCaptainRecs({})
    } finally {
      // Only reset if not aborted
      if (!abortController.signal.aborted) {
        setLoadingTripleCaptain(false)
      }
      tcAbortControllerRef.current = null
    }
  }

  useEffect(() => {
    // Lazy-load heavy tabs only when the user opens them
    if (activeTab === 'picks') ensurePicksLoaded()
    if (activeTab === 'differentials') ensureDifferentialsLoaded()
    if (activeTab === 'wildcard') {
      // Load immediately when tab is clicked
      loadWildcardTrajectory()
    }
    if (activeTab === 'triple-captain') {
      ensureTripleCaptainLoaded()
    } else {
      // Cleanup when navigating away from Triple Captain tab
      // Cancel any ongoing requests
      if (tcAbortControllerRef.current) {
        tcAbortControllerRef.current.abort()
        tcAbortControllerRef.current = null
      }
      // Reset loading state if we're not on the tab
      if (loadingTripleCaptain) {
        setLoadingTripleCaptain(false)
      }
    }
    // Reload tasks from localStorage when tasks tab becomes active
    // This ensures tasks are always up-to-date when viewing the tab
    if (activeTab === 'tasks') {
      loadTasksFromStorage()
    }
    
    // Cleanup on unmount
    return () => {
      if (tcAbortControllerRef.current) {
        tcAbortControllerRef.current.abort()
        tcAbortControllerRef.current = null
      }
      // Clear polling interval on unmount
      if (tcPollingInterval) {
        clearInterval(tcPollingInterval)
        setTcPollingInterval(null)
      }
      // Reset loading states on unmount
      setLoadingTripleCaptain(false)
    }
  }, [activeTab, loadTasksFromStorage])

  const refresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      const gwRes = await fetch(`${API_BASE}/api/gameweek`).then(r => r.json())
      setGameweek(gwRes)
    } catch (err) {
      console.error('Gameweek refresh error:', err)
    }

    // Refresh only the currently active heavy tab to keep UX snappy.
    try {
      if (activeTab === 'picks') {
        setTopPicks({})
        const res = await fetch(`${API_BASE}/api/top-picks`).then(r => r.json())
        setTopPicks(res)
      } else if (activeTab === 'differentials') {
        setDifferentials([])
        const res = await fetch(`${API_BASE}/api/differentials`).then(r => r.json())
        setDifferentials(res.differentials || [])
      } else if (activeTab === 'triple-captain') {
        setTripleCaptainRecs({})
        setSelectedTcGameweekTab(null)
        await ensureTripleCaptainLoaded()
      }
    } catch (err) {
      console.error('Refresh error:', err)
    } finally {
      setRefreshing(false)
    }
  }

  // Search behavior:
  // - If query has 2+ chars: name search
  // - If query is empty: show cheapest players for selected position (bench fodder)
  const searchPlayers = async (query: string, position?: string) => {
    const trimmed = query.trim()
    if (trimmed.length > 0 && trimmed.length < 2) {
      setSearchResults([])
      return
    }
    
    try {
      const url = `${API_BASE}/api/players/search?q=${encodeURIComponent(trimmed)}${position ? `&position=${position}` : ''}`
      const res = await fetch(url)
      const data = await res.json()
      setSearchResults(data.players || [])
    } catch (err) {
      console.error('Search error:', err)
    }
  }

  // FPL position limits
  const POSITION_LIMITS: Record<string, number> = {
    'GK': 2,
    'DEF': 5,
    'MID': 5,
    'FWD': 3,
  }

  const getPositionCount = (position: string) => {
    return mySquad.filter(p => p.position === position).length
  }

  const isPositionFull = (position: string) => {
    const limit = POSITION_LIMITS[position] || 0
    return getPositionCount(position) >= limit
  }

  const canAddPlayer = (player: Player) => {
    if (mySquad.length >= 15) return false
    if (mySquad.find(p => p.id === player.id)) return false
    if (isPositionFull(player.position)) return false
    return true
  }

  const addToSquad = (player: Player) => {
    if (!canAddPlayer(player)) return
    
    // Find the first available slot for this position
    const positionPlayers = mySquad.filter(p => p.position === player.position)
    const maxSlots = POSITION_LIMITS[player.position] || 0
    let slotIndex = positionPlayers.length
    if (slotIndex >= maxSlots) slotIndex = maxSlots - 1
    
    const newPlayer: SquadPlayer = {
      id: player.id,
      name: player.name,
      position: player.position,
      // Default to CURRENT price; user can edit to their SELLING price.
      price: typeof player.price === 'number' ? Math.round(player.price * 10) / 10 : 0,
      team: player.team,
      rotation_risk: player.rotation_risk,
      european_comp: player.european_comp,
    }
    
    setMySquad([...mySquad, newPlayer])
    setPlayerSlotPositions(new Map(playerSlotPositions.set(player.id, { position: player.position, slotIndex })))
    setSearchQuery('')
    setSearchResults([])
  }

  const removeFromSquad = (playerId: number) => {
    setMySquad(mySquad.filter(p => p.id !== playerId))
    const newMap = new Map(playerSlotPositions)
    newMap.delete(playerId)
    setPlayerSlotPositions(newMap)
  }

  const updateSquadPrice = (playerId: number, newPrice: number) => {
    const price = Number.isFinite(newPrice) ? Math.round(newPrice * 10) / 10 : 0
    setMySquad(mySquad.map(p => p.id === playerId ? { ...p, price } : p))
  }

  const getTransferSuggestions = async () => {
    if (mySquad.length < 11) {
      alert('Please add at least 11 players to your squad')
      return
    }
    
    setTransferLoading(true)
    
    try {
      // Request enough suggestions to get N different players (N = free transfers)
      // Multiply by 3 to account for multiple options per player that will be grouped
      const suggestionsLimit = Math.max(3, (Number.isFinite(freeTransfers) ? freeTransfers : 1) * 3)
      const res = await fetch(`${API_BASE}/api/transfer-suggestions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          squad: mySquad,
          bank: bank,
          free_transfers: freeTransfers,
          suggestions_limit: suggestionsLimit,
        }),
      })
      
      const data = await res.json()
      setTransferSuggestions(data.suggestions || [])
      setSquadAnalysis(data.squad_analysis || [])
      
      // Scroll to results after a short delay to ensure DOM is updated
      setTimeout(() => {
        if (resultsSectionRef.current) {
          const element = resultsSectionRef.current
          const elementPosition = element.getBoundingClientRect().top + window.pageYOffset
          const offsetPosition = elementPosition + 100 // Scroll 100px more down to show full content
          window.scrollTo({
            top: offsetPosition,
            behavior: 'smooth'
          })
        }
      }, 100)
    } catch (err) {
      console.error('Transfer suggestion error:', err)
    } finally {
      setTransferLoading(false)
    }
  }

  const getPositionClass = (pos: string) => {
    const classes: Record<string, string> = {
      'GK': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
      'DEF': 'bg-green-500/20 text-green-400 border-green-500/30',
      'MID': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
      'FWD': 'bg-red-500/20 text-red-400 border-red-500/30'
    }
    return classes[pos] || 'bg-gray-500/20 text-gray-400'
  }

  // Parse formation string (e.g., "3-5-2" -> {def: 3, mid: 5, fwd: 2})
  const parseFormation = (formation: string) => {
    const parts = formation.split('-').map(Number)
    return {
      def: parts[0] || 0,
      mid: parts[1] || 0,
      fwd: parts[2] || 0,
      gk: 1
    }
  }

  // Render a single player pill (uniform size)
  const renderPlayerPill = (player: any | null, isEmpty: boolean = false, showRemoveButton: boolean = false) => {
    const pillClasses = "flex flex-col items-center justify-center p-2 sm:p-3 rounded-lg border-2 w-[90px] sm:w-[110px] h-[100px] sm:h-[120px] transition-all"
    
    if (isEmpty || !player) {
      return (
        <div className={`${pillClasses} bg-[#0b0b14]/50 border-[#2a2a4a]/30 border-dashed opacity-50`}>
          <div className="text-gray-500 text-[10px] text-center">Empty</div>
        </div>
      )
    }

    return (
      <div
        className={`${pillClasses} relative ${
          player.is_captain 
            ? 'bg-yellow-500/30 border-yellow-400 shadow-lg shadow-yellow-500/20' 
            : player.is_vice_captain
            ? 'bg-purple-500/30 border-purple-400 shadow-lg shadow-purple-500/20'
            : player.rotation_risk === 'high'
            ? 'bg-orange-500/20 border-orange-500/50'
            : 'bg-[#0f0f1a]/80 border-[#2a2a4a] hover:border-[#00ff87]/50'
        }`}
      >
        {/* Remove button - top right */}
        {showRemoveButton && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              removeFromSquad(player.id)
            }}
            className="absolute top-1 right-1 w-4 h-4 flex items-center justify-center rounded-full bg-red-500/80 hover:bg-red-500 text-white opacity-80 hover:opacity-100 transition-opacity z-10"
            title="Remove player"
          >
            <X className="w-3 h-3" />
          </button>
        )}
        <div className="flex items-center gap-1 mb-1 flex-wrap justify-center">
          {player.is_captain && <span className="text-yellow-400 font-bold text-[10px]">©</span>}
          {player.is_vice_captain && <span className="text-purple-400 font-bold text-[10px]">V</span>}
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${getPositionClass(player.position)}`}>
            {player.position}
          </span>
        </div>
        <div className="font-medium text-[11px] sm:text-xs text-center truncate w-full leading-tight">{player.name}</div>
        <div className="text-[9px] text-gray-400 truncate w-full text-center mt-0.5">{player.team}</div>
        {player.predicted !== undefined && (
          <div className="text-[9px] text-[#00ff87] font-mono mt-1">{player.predicted?.toFixed(1) ?? '0.0'}</div>
        )}
        {player.european_comp && (
          <span className={`mt-1 px-1 py-0.5 rounded text-[8px] font-bold ${
            player.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
            player.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
            'bg-blue-500/20 text-blue-400'
          }`}>
            {player.european_comp}
          </span>
        )}
      </div>
    )
  }

  // Render player pill with transfer highlighting (red for out, green for in)
  const renderPlayerPillWithTransfer = (player: any | null, isEmpty: boolean, isTransferOut: boolean, isTransferIn: boolean) => {
    const pillClasses = "flex flex-col items-center justify-center p-2 sm:p-3 rounded-lg border-2 w-[90px] sm:w-[110px] h-[100px] sm:h-[120px] transition-all"
    
    if (isEmpty || !player) {
      return (
        <div className={`${pillClasses} bg-[#0b0b14]/50 border-[#2a2a4a]/30 border-dashed opacity-50`}>
          <div className="text-gray-500 text-[10px] text-center">Empty</div>
        </div>
      )
    }

    // Determine border color based on transfer status
    let borderClass = 'border-[#2a2a4a]'
    let bgClass = 'bg-[#0f0f1a]/80'
    if (isTransferOut) {
      borderClass = 'border-red-400'
      bgClass = 'bg-red-500/20'
    } else if (isTransferIn) {
      borderClass = 'border-green-400'
      bgClass = 'bg-green-500/20'
    }

    return (
      <div
        className={`${pillClasses} ${bgClass} ${borderClass} ${
          player.is_captain 
            ? 'shadow-lg shadow-yellow-500/20' 
            : player.is_vice_captain
            ? 'shadow-lg shadow-purple-500/20'
            : ''
        }`}
      >
        <div className="flex items-center gap-1 mb-1 flex-wrap justify-center">
          {player.is_captain && <span className="text-yellow-400 font-bold text-[10px]">©</span>}
          {player.is_vice_captain && <span className="text-purple-400 font-bold text-[10px]">V</span>}
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${getPositionClass(player.position)}`}>
            {player.position}
          </span>
        </div>
        <div className="font-medium text-[11px] sm:text-xs text-center truncate w-full leading-tight">{player.name}</div>
        <div className="text-[9px] text-gray-400 truncate w-full text-center mt-0.5">{player.team}</div>
        {player.predicted !== undefined && (
          <div className="text-[9px] text-[#00ff87] font-mono mt-1">{player.predicted?.toFixed(1) ?? '0.0'}</div>
        )}
        {player.european_comp && (
          <span className={`mt-1 px-1 py-0.5 rounded text-[8px] font-bold ${
            player.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
            player.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
            'bg-blue-500/20 text-blue-400'
          }`}>
            {player.european_comp}
          </span>
        )}
      </div>
    )
  }

  // Render before/after pitch formations side by side (all 15 players on field)
  const renderBeforeAfterPitch = (
    beforeSquad: any[], // Full 15-player squad
    afterSquad: any[], // Full 15-player squad
    beforeFormation: string,
    afterFormation: string,
    transfersOut: any[],
    transfersIn: any[]
  ) => {
    const beforeFormationLayout = parseFormation(beforeFormation)
    const afterFormationLayout = parseFormation(afterFormation)
    
    // Group players by position for before squad (all 15 players)
    const beforeByPosition = {
      GK: beforeSquad.filter((p: any) => p.position === 'GK'),
      DEF: beforeSquad.filter((p: any) => p.position === 'DEF'),
      MID: beforeSquad.filter((p: any) => p.position === 'MID'),
      FWD: beforeSquad.filter((p: any) => p.position === 'FWD'),
    }

    // Group players by position for after squad (all 15 players)
    const afterByPosition = {
      GK: afterSquad.filter((p: any) => p.position === 'GK'),
      DEF: afterSquad.filter((p: any) => p.position === 'DEF'),
      MID: afterSquad.filter((p: any) => p.position === 'MID'),
      FWD: afterSquad.filter((p: any) => p.position === 'FWD'),
    }

    // Create transfer lookup maps
    const transferOutMap = new Set(transfersOut.map((t: any) => t.id))
    const transferInMap = new Set(transfersIn.map((t: any) => t.id))

    const renderRow = (players: any[], expectedCount: number, position: string, isBefore: boolean) => {
      const slots: any[] = []
      for (let i = 0; i < expectedCount; i++) {
        if (i < players.length) {
          const player = players[i]
          const isTransferOut = isBefore && transferOutMap.has(player.id)
          const isTransferIn = !isBefore && transferInMap.has(player.id)
          slots.push({ player, isTransferOut, isTransferIn })
        } else {
          slots.push({ player: null, isTransferOut: false, isTransferIn: false })
        }
      }
      return slots
    }

    const renderPitchSide = (byPosition: any, formationLayout: any, isBefore: boolean, title: string) => {
      // Show all players on the field - starting XI in formation, bench players below
      // Starting XI positions
      const startingXiGK = byPosition.GK.slice(0, 1)
      const startingXiDEF = byPosition.DEF.slice(0, formationLayout.def)
      const startingXiMID = byPosition.MID.slice(0, formationLayout.mid)
      const startingXiFWD = byPosition.FWD.slice(0, formationLayout.fwd)
      
      // Bench players (remaining after starting XI)
      const benchGK = byPosition.GK.slice(1)
      const benchDEF = byPosition.DEF.slice(formationLayout.def)
      const benchMID = byPosition.MID.slice(formationLayout.mid)
      const benchFWD = byPosition.FWD.slice(formationLayout.fwd)
      
      // Combine all bench players in order: GK, DEF, MID, FWD
      const benchPlayers = [...benchGK, ...benchDEF, ...benchMID, ...benchFWD]
      
      return (
        <div className="flex-1">
          <div className="text-sm font-semibold mb-3 text-center" style={{ color: isBefore ? '#f87171' : '#00ff87' }}>
            {title}
          </div>
          <div className="bg-gradient-to-b from-green-900/20 via-green-800/10 to-green-900/20 rounded-lg border border-green-500/20 p-3 sm:p-4 md:p-6">
            <div className="relative min-h-[400px] sm:min-h-[500px] md:min-h-[550px] flex flex-col justify-between">
              {/* Goalkeeper (TOP) */}
              <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4">
                {renderRow(startingXiGK, 1, 'GK', isBefore).map((slot, idx) => (
                  <div key={`gk-${idx}`}>
                    {renderPlayerPillWithTransfer(slot.player, slot.player === null, slot.isTransferOut, slot.isTransferIn)}
                  </div>
                ))}
                {/* Bench GK */}
                {benchGK.map((player: any) => {
                  const isTransferOut = isBefore && transferOutMap.has(player.id)
                  const isTransferIn = !isBefore && transferInMap.has(player.id)
                  return (
                    <div key={`bench-gk-${player.id}`}>
                      {renderPlayerPillWithTransfer(player, false, isTransferOut, isTransferIn)}
                    </div>
                  )
                })}
              </div>

              {/* Defenders */}
              <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
                {renderRow(startingXiDEF, formationLayout.def, 'DEF', isBefore).map((slot, idx) => (
                  <div key={`def-${idx}`}>
                    {renderPlayerPillWithTransfer(slot.player, slot.player === null, slot.isTransferOut, slot.isTransferIn)}
                  </div>
                ))}
                {/* Bench DEF */}
                {benchDEF.map((player: any) => {
                  const isTransferOut = isBefore && transferOutMap.has(player.id)
                  const isTransferIn = !isBefore && transferInMap.has(player.id)
                  return (
                    <div key={`bench-def-${player.id}`}>
                      {renderPlayerPillWithTransfer(player, false, isTransferOut, isTransferIn)}
                    </div>
                  )
                })}
              </div>

              {/* Midfielders */}
              <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
                {renderRow(startingXiMID, formationLayout.mid, 'MID', isBefore).map((slot, idx) => (
                  <div key={`mid-${idx}`}>
                    {renderPlayerPillWithTransfer(slot.player, slot.player === null, slot.isTransferOut, slot.isTransferIn)}
                  </div>
                ))}
                {/* Bench MID */}
                {benchMID.map((player: any) => {
                  const isTransferOut = isBefore && transferOutMap.has(player.id)
                  const isTransferIn = !isBefore && transferInMap.has(player.id)
                  return (
                    <div key={`bench-mid-${player.id}`}>
                      {renderPlayerPillWithTransfer(player, false, isTransferOut, isTransferIn)}
                    </div>
                  )
                })}
              </div>

              {/* Forwards (BOTTOM) */}
              <div className="flex justify-center items-center gap-2 sm:gap-3 flex-wrap">
                {renderRow(startingXiFWD, formationLayout.fwd, 'FWD', isBefore).map((slot, idx) => (
                  <div key={`fwd-${idx}`}>
                    {renderPlayerPillWithTransfer(slot.player, slot.player === null, slot.isTransferOut, slot.isTransferIn)}
                  </div>
                ))}
                {/* Bench FWD */}
                {benchFWD.map((player: any) => {
                  const isTransferOut = isBefore && transferOutMap.has(player.id)
                  const isTransferIn = !isBefore && transferInMap.has(player.id)
                  return (
                    <div key={`bench-fwd-${player.id}`}>
                      {renderPlayerPillWithTransfer(player, false, isTransferOut, isTransferIn)}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {renderPitchSide(beforeByPosition, beforeFormationLayout, true, 'Before')}
        {renderPitchSide(afterByPosition, afterFormationLayout, false, 'After')}
      </div>
    )
  }

  // Render pitch formation view (GK top, FWD bottom)
  const renderPitchFormation = (startingXi: any[], formation: string, showEmptySlots: boolean = false) => {
    const formationLayout = parseFormation(formation)
    
    // Group players by position
    const byPosition = {
      GK: startingXi.filter((p: any) => p.position === 'GK'),
      DEF: startingXi.filter((p: any) => p.position === 'DEF'),
      MID: startingXi.filter((p: any) => p.position === 'MID'),
      FWD: startingXi.filter((p: any) => p.position === 'FWD'),
    }

    // Create arrays with empty slots if needed
    const renderRow = (players: any[], expectedCount: number, position: string) => {
      const slots: any[] = []
      for (let i = 0; i < expectedCount; i++) {
        if (i < players.length) {
          slots.push(players[i])
        } else if (showEmptySlots) {
          slots.push(null) // null indicates empty slot
        }
      }
      return slots
    }

    return (
      <div className="bg-gradient-to-b from-green-900/20 via-green-800/10 to-green-900/20 rounded-lg border border-green-500/20 p-3 sm:p-4 md:p-6">
        {/* Pitch - Rotated: GK top, FWD bottom */}
        <div className="relative min-h-[350px] sm:min-h-[450px] md:min-h-[500px] flex flex-col justify-between">
          {/* Goalkeeper (TOP) */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4">
            {renderRow(byPosition.GK, 1, 'GK').map((slot, idx) => (
              <div key={`gk-${idx}`}>
                {renderPlayerPill(slot, slot === null)}
              </div>
            ))}
          </div>

          {/* Defenders */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
            {renderRow(byPosition.DEF, formationLayout.def, 'DEF').map((slot, idx) => (
              <div key={`def-${idx}`}>
                {renderPlayerPill(slot, slot === null)}
              </div>
            ))}
          </div>

          {/* Midfielders */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
            {renderRow(byPosition.MID, formationLayout.mid, 'MID').map((slot, idx) => (
              <div key={`mid-${idx}`}>
                {renderPlayerPill(slot, slot === null)}
              </div>
            ))}
          </div>

          {/* Forwards (BOTTOM) */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 flex-wrap">
            {renderRow(byPosition.FWD, formationLayout.fwd, 'FWD').map((slot, idx) => (
              <div key={`fwd-${idx}`}>
                {renderPlayerPill(slot, slot === null)}
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // Render transfers pitch with empty slots based on current squad
  const renderTransfersPitch = () => {
    // Use standard FPL formation (3-5-2) to show all slots - 2 GKs allowed
    const formation = { def: 5, mid: 5, fwd: 3, gk: 2 }

    // Group current squad by position and maintain slot positions
    // Create slot-based structure to preserve positions when removing players
    const getSlotsForPosition = (position: string, maxSlots: number) => {
      const players = mySquad.filter(p => p.position === position)
      const slots: (SquadPlayer | null)[] = new Array(maxSlots).fill(null)
      const pendingUpdates = new Map<number, { position: string; slotIndex: number }>()
      
      // Fill slots based on stored slot positions, preserving positions when players are removed
      players.forEach((player) => {
        const slotInfo = playerSlotPositions.get(player.id)
        if (slotInfo && slotInfo.slotIndex < maxSlots && slots[slotInfo.slotIndex] === null) {
          // Use stored slot position if available
          slots[slotInfo.slotIndex] = player
        } else {
          // If no stored position or slot is taken, find first available slot
          for (let i = 0; i < maxSlots; i++) {
            if (slots[i] === null) {
              slots[i] = player
              // Track pending update (will be applied after render)
              if (!slotInfo || slotInfo.slotIndex !== i) {
                pendingUpdates.set(player.id, { position, slotIndex: i })
              }
              break
            }
          }
        }
      })
      
      // Apply pending updates after render completes
      if (pendingUpdates.size > 0) {
        setTimeout(() => {
          const newMap = new Map(playerSlotPositions)
          pendingUpdates.forEach((value, key) => {
            newMap.set(key, value)
          })
          setPlayerSlotPositions(newMap)
        }, 0)
      }
      
      return slots
    }

    const byPosition = {
      GK: getSlotsForPosition('GK', formation.gk),
      DEF: getSlotsForPosition('DEF', formation.def),
      MID: getSlotsForPosition('MID', formation.mid),
      FWD: getSlotsForPosition('FWD', formation.fwd),
    }

    const handleSlotClick = (position: string) => {
      if (isPositionFull(position)) return
      // Focus search and filter by position
      setSearchPosition(position)
      if (!searchQuery.trim()) {
        searchPlayers('', position)
      }
    }

    return (
      <div className="bg-gradient-to-b from-green-900/20 via-green-800/10 to-green-900/20 rounded-lg border border-green-500/20 p-3 sm:p-4 md:p-6">
        <div className="relative min-h-[350px] sm:min-h-[450px] md:min-h-[500px] flex flex-col justify-between">
          {/* Goalkeeper (TOP) */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4">
            {byPosition.GK.map((slot, idx) => {
              const isEmpty = slot === null
              const isFull = isPositionFull('GK')
              return (
                <div 
                  key={`gk-${idx}`} 
                  onClick={() => isEmpty && !isFull && handleSlotClick('GK')} 
                  className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                  title={isEmpty && !isFull ? 'Click to search for GK players' : isEmpty && isFull ? 'GK position full' : ''}
                >
                  {renderPlayerPill(slot, isEmpty, true)}
                </div>
              )
            })}
          </div>

          {/* Defenders */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
            {byPosition.DEF.map((slot, idx) => {
              const isEmpty = slot === null
              const isFull = isPositionFull('DEF')
              return (
                <div 
                  key={`def-${idx}`} 
                  onClick={() => isEmpty && !isFull && handleSlotClick('DEF')} 
                  className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                  title={isEmpty && !isFull ? 'Click to search for DEF players' : isEmpty && isFull ? 'DEF position full' : ''}
                >
                  {renderPlayerPill(slot, isEmpty, true)}
                </div>
              )
            })}
          </div>

          {/* Midfielders */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
            {byPosition.MID.map((slot, idx) => {
              const isEmpty = slot === null
              const isFull = isPositionFull('MID')
              return (
                <div 
                  key={`mid-${idx}`} 
                  onClick={() => isEmpty && !isFull && handleSlotClick('MID')} 
                  className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                  title={isEmpty && !isFull ? 'Click to search for MID players' : isEmpty && isFull ? 'MID position full' : ''}
                >
                  {renderPlayerPill(slot, isEmpty, true)}
                </div>
              )
            })}
          </div>

          {/* Forwards (BOTTOM) */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 flex-wrap">
            {byPosition.FWD.map((slot, idx) => {
              const isEmpty = slot === null
              const isFull = isPositionFull('FWD')
              return (
                <div 
                  key={`fwd-${idx}`} 
                  onClick={() => isEmpty && !isFull && handleSlotClick('FWD')} 
                  className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                  title={isEmpty && !isFull ? 'Click to search for FWD players' : isEmpty && isFull ? 'FWD position full' : ''}
                >
                  {renderPlayerPill(slot, isEmpty, true)}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

  const formatDeadline = (dateStr?: string) => {
    if (!dateStr) return 'Unknown'
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-GB', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  const navigationTabs = [
    { id: 'transfers', icon: ArrowRightLeft, label: 'Transfers', shortLabel: 'Transfers', color: 'text-blue-400', description: 'Get AI-powered transfer suggestions (1-3) or coordinated rebuild (4+)' },
    { id: 'wildcard', icon: Zap, label: 'Wildcard', shortLabel: 'WC', color: 'text-violet-400', description: '8-GW trajectory optimizer using hybrid LSTM-XGBoost model' },
    { id: 'selected_teams', icon: Trophy, label: 'Free Hit of the Week', shortLabel: 'Free Hit', color: 'text-yellow-400', description: 'View your saved free hit team selections' },
    { id: 'triple-captain', icon: Crown, label: 'Triple Captain', shortLabel: 'TC', color: 'text-purple-400', description: 'Find optimal gameweeks to use Triple Captain chip' },
    { id: 'picks', icon: Star, label: 'Top Picks', shortLabel: 'Picks', color: 'text-yellow-400', description: 'Top player picks by position' },
    { id: 'differentials', icon: Target, label: 'Differentials', shortLabel: 'Diffs', color: 'text-pink-400', description: 'Low ownership, high potential players' },
    { id: 'tasks', icon: Clock, label: 'Tasks', shortLabel: 'Tasks', color: 'text-cyan-400', description: 'Track background tasks and their progress' },
  ]


  if (loading) {
    return (
      <div className="min-h-screen bg-[#0f0f1a] flex items-center justify-center">
        <div className="text-center">
          <RefreshCw className="w-10 h-10 animate-spin text-[#00ff87] mx-auto mb-4" />
          <p className="text-gray-400">Loading predictions...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-[#0f0f1a] flex items-center justify-center p-4">
        <div className="card max-w-md w-full text-center">
          <p className="text-red-400 mb-4">{error}</p>
          <button onClick={loadInitial} className="btn btn-primary">
            Try Again
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0f0f1a] text-white flex">
      {/* Left Sidebar Navigation - Desktop Only */}
      <aside className="hidden md:flex flex-col w-64 bg-[#1a1a2e] border-r border-[#2a2a4a] sticky top-0 h-screen overflow-y-auto">
        <div className="px-6 py-4 border-b border-[#2a2a4a]">
          <div className="flex items-center gap-3 h-10">
            <div className="w-10 h-10 bg-gradient-to-br from-[#38003c] to-[#00ff87] rounded-lg flex items-center justify-center shadow-lg border border-[#00ff87]/20 flex-shrink-0">
              <FPLLogo className="w-6 h-6" />
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="font-bold text-sm leading-tight">FPL Squad Suggester</h1>
              <p className="text-[10px] text-gray-400 leading-tight">
                {gameweek?.next?.id ? `GW${gameweek.next.id}` : 'Loading...'}
              </p>
            </div>
          </div>
        </div>
        <nav className="flex-1 p-2">
          <button
            onClick={() => setActiveTab('home')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg mb-2 transition-colors ${
              activeTab === 'home'
                ? 'bg-[#00ff87]/10 text-[#00ff87] border border-[#00ff87]/30'
                : 'text-gray-400 hover:text-white hover:bg-[#1a1a2e]/50'
            }`}
          >
            <Home className={`w-5 h-5 ${activeTab === 'home' ? 'text-[#00ff87]' : 'text-gray-400'}`} />
            <span className="text-sm font-medium">Home</span>
          </button>
          {navigationTabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 transition-colors ${
                activeTab === tab.id
                  ? 'bg-[#00ff87]/10 text-[#00ff87] border border-[#00ff87]/30'
                  : 'text-gray-400 hover:text-white hover:bg-[#1a1a2e]/50'
              }`}
            >
              <tab.icon className={`w-5 h-5 flex-shrink-0 ${activeTab === tab.id ? tab.color : ''}`} />
              <span className="text-sm font-medium">{tab.label}</span>
              {tab.id === 'tasks' && tasks.some(task => task.status === 'running') && (
                <Loader2 className="w-4 h-4 text-[#00ff87] animate-spin ml-auto flex-shrink-0" />
              )}
            </button>
          ))}
        </nav>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile Header */}
        <header className="md:hidden bg-[#1a1a2e] border-b border-[#2a2a4a] px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <button
              onClick={() => setActiveTab('home')}
              className="flex items-center gap-2"
            >
              <div className="w-10 h-10 bg-gradient-to-br from-[#38003c] to-[#00ff87] rounded-lg flex items-center justify-center shadow-lg border border-[#00ff87]/20">
                <FPLLogo className="w-6 h-6" />
              </div>
              <div className="flex-1 min-w-0">
                <h1 className="font-bold text-sm">FPL Squad Suggester</h1>
                {gameweek?.next && countdown ? (
                  <div className="flex items-center gap-1 mt-0.5">
                    <span className="text-[10px] text-gray-300 font-bold">GW{gameweek.next.id}</span>
                    <span className="text-[10px] text-gray-600 mx-0.5">•</span>
                    {countdown.days > 0 && (
                      <>
                        <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.5)]">{countdown.days}d</span>
                        <span className="text-[10px] text-gray-600">:</span>
                      </>
                    )}
                    <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.5)]">{String(countdown.hours).padStart(2, '0')}</span>
                    <span className="text-[10px] text-gray-600">:</span>
                    <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.5)]">{String(countdown.minutes).padStart(2, '0')}</span>
                    <span className="text-[10px] text-gray-600">:</span>
                    <span className="text-[10px] font-bold text-[#00ff87] drop-shadow-[0_0_4px_rgba(0,255,135,0.6)] animate-pulse">{String(countdown.seconds).padStart(2, '0')}</span>
                  </div>
                ) : (
                  <p className="text-[10px] text-gray-400 animate-pulse">
                    {gameweek?.next?.id ? `GW${gameweek.next.id}` : 'Loading...'}
                  </p>
                )}
              </div>
            </button>
            {activeTab !== 'selected_teams' && activeTab !== 'home' && (
          <button 
            onClick={refresh} 
            disabled={refreshing}
                className="btn btn-secondary flex items-center gap-1 text-xs px-3 py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
                <RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin' : ''}`} />
                <span>{refreshing ? 'Refreshing...' : 'Refresh'}</span>
          </button>
            )}
        </div>
      </header>

        {/* Mobile Navigation */}
        {activeTab !== 'home' && (
          <nav className="md:hidden bg-[#1a1a2e]/50 border-b border-[#2a2a4a] px-4 overflow-x-auto scrollbar-hide">
            <div className="flex gap-1 min-w-max py-2">
              {navigationTabs.map(tab => {
                return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                    className={`flex items-center gap-1 px-3 py-2 border-b-2 transition-colors whitespace-nowrap ${
                  activeTab === tab.id 
                    ? 'border-[#00ff87] text-white' 
                    : 'border-transparent text-gray-400 hover:text-white'
                }`}
              >
                    <tab.icon className="w-4 h-4 flex-shrink-0" />
                    <span className="text-xs">{tab.shortLabel}</span>
              </button>
                )
              })}
        </div>
      </nav>
        )}

        {/* Desktop Header */}
        <header className="hidden md:block bg-[#1a1a2e] border-b border-[#2a2a4a] px-6 py-4">
          <div className="flex items-center justify-between h-10">
            <div className="flex items-center gap-4 h-full">
              {gameweek?.next && (
                <>
                  <div className="text-gray-300 font-bold text-lg tracking-wide flex items-center h-full">
                    GW{gameweek.next.id}
                  </div>
                  {countdown && (
                    <>
                      <div className="h-6 w-px bg-gradient-to-b from-transparent via-[#00ff87]/30 to-transparent flex items-center"></div>
                      <div className="flex items-center gap-2 h-full">
                      {countdown.days > 0 && (
                        <div className="flex items-center gap-1.5 h-full">
                          <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/30 border border-[#00ff87]/20 transition-all duration-300 hover:scale-105 hover:shadow-[#00ff87]/50 flex items-center justify-center">
                            <div className="absolute inset-0 bg-gradient-to-br from-[#00ff87]/20 to-transparent rounded-lg animate-pulse"></div>
                            <span className="relative z-10 drop-shadow-sm">{countdown.days}</span>
                          </div>
                          <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">d</span>
                        </div>
                      )}
                      <div className="flex items-center gap-1.5 h-full">
                        <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/30 border border-[#00ff87]/20 transition-all duration-300 hover:scale-105 hover:shadow-[#00ff87]/50 flex items-center justify-center">
                          <div className="absolute inset-0 bg-gradient-to-br from-[#00ff87]/20 to-transparent rounded-lg animate-pulse"></div>
                          <span className="relative z-10 drop-shadow-sm">{String(countdown.hours).padStart(2, '0')}</span>
                        </div>
                        <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">h</span>
                      </div>
                      <div className="flex items-center gap-1.5 h-full">
                        <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/30 border border-[#00ff87]/20 transition-all duration-300 hover:scale-105 hover:shadow-[#00ff87]/50 flex items-center justify-center">
                          <div className="absolute inset-0 bg-gradient-to-br from-[#00ff87]/20 to-transparent rounded-lg animate-pulse"></div>
                          <span className="relative z-10 drop-shadow-sm">{String(countdown.minutes).padStart(2, '0')}</span>
                        </div>
                        <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">m</span>
                      </div>
                      <div className="flex items-center gap-1.5 h-full">
                        <div className="relative bg-gradient-to-br from-[#38003c] via-[#6a0080] to-[#00ff87] text-white px-3 py-1.5 rounded-lg font-bold text-sm min-w-[3.5rem] text-center shadow-lg shadow-[#00ff87]/40 border border-[#00ff87]/30 transition-all duration-300 hover:scale-105 hover:shadow-[#00ff87]/60 flex items-center justify-center">
                          <div className="absolute inset-0 bg-gradient-to-br from-[#00ff87]/30 to-transparent rounded-lg animate-ping opacity-20"></div>
                          <div className="absolute inset-0 bg-gradient-to-br from-[#00ff87]/20 to-transparent rounded-lg animate-pulse"></div>
                          <span className="relative z-10 drop-shadow-sm">{String(countdown.seconds).padStart(2, '0')}</span>
                        </div>
                        <span className="text-gray-400 text-xs font-semibold uppercase tracking-wider flex items-center">s</span>
                      </div>
                    </div>
                    </>
                  )}
                  {!countdown && <div className="text-gray-400 text-sm animate-pulse flex items-center h-full">Loading...</div>}
                </>
              )}
              {!gameweek?.next && <div className="text-gray-400 text-sm animate-pulse flex items-center h-full">Loading...</div>}
            </div>
            {activeTab !== 'selected_teams' && activeTab !== 'home' && (
              <button 
                onClick={refresh} 
                disabled={refreshing}
                className="btn btn-secondary flex items-center gap-2 text-sm px-4 py-2 disabled:opacity-50 disabled:cursor-not-allowed h-full"
              >
                <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
                <span>{refreshing ? 'Refreshing...' : 'Refresh'}</span>
              </button>
            )}
          </div>
        </header>

      {/* Content */}
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">
        
        {/* Home Page */}
        {activeTab === 'home' && (
          <div className="max-w-6xl mx-auto">
            <div className="mb-8">
              <h2 className="text-2xl font-bold mb-2">Welcome to FPL Squad Suggester</h2>
              <p className="text-gray-400">Choose a section to get started</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {navigationTabs.map(tab => {
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className="group relative p-6 bg-[#1a1a2e] rounded-lg border border-[#2a2a4a] hover:border-[#00ff87]/50 transition-all hover:shadow-lg hover:shadow-[#00ff87]/10 text-left"
                  >
                    <div className="flex items-start gap-4">
                      <div className={`p-3 rounded-lg bg-[#0f0f1a] ${tab.color} group-hover:scale-110 transition-transform flex-shrink-0`}>
                        <tab.icon className="w-6 h-6" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 className="font-semibold text-lg mb-1 group-hover:text-[#00ff87] transition-colors break-words leading-tight">
                          {tab.label}
                        </h3>
                        <p className="text-sm text-gray-400 break-words leading-relaxed">
                          {tab.description}
                        </p>
                      </div>
                      <ChevronRight className="w-5 h-5 text-gray-500 group-hover:text-[#00ff87] group-hover:translate-x-1 transition-all flex-shrink-0" />
                  </div>
                  </button>
                )
              })}
            </div>
          </div>
        )}
        

        {/* Transfers Tab */}
        {activeTab === 'transfers' && (
          <TransfersTab
            mySquad={mySquad}
            setMySquad={setMySquad}
            bank={bank}
            setBank={setBank}
            bankInput={bankInput}
            setBankInput={setBankInput}
            freeTransfers={freeTransfers}
            setFreeTransfers={setFreeTransfers}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            searchPosition={searchPosition}
            setSearchPosition={setSearchPosition}
            searchResults={searchResults}
            searchPlayers={searchPlayers}
            addToSquad={addToSquad}
            removeFromSquad={removeFromSquad}
            updateSquadPrice={updateSquadPrice}
            isPositionFull={isPositionFull}
            getPositionCount={getPositionCount}
            transferSuggestions={transferSuggestions}
            setTransferSuggestions={setTransferSuggestions}
            groupedTransferSuggestions={groupedTransferSuggestions}
            transferLoading={transferLoading}
            getTransferSuggestions={getTransferSuggestions}
            wildcardPlan={wildcardPlan}
            setWildcardPlan={setWildcardPlan}
            wildcardLoading={wildcardLoading}
            setWildcardLoading={setWildcardLoading}
            expandedGroups={expandedGroups}
            setExpandedGroups={setExpandedGroups}
            squadAnalysis={squadAnalysis}
            error={error}
            setError={setError}
            savedFplTeams={savedFplTeams}
            selectedSavedFplTeamId={selectedSavedFplTeamId}
            setSelectedSavedFplTeamId={setSelectedSavedFplTeamId}
            fplTeamId={fplTeamId}
            setFplTeamId={setFplTeamId}
            importingFplTeam={importingFplTeam}
            importFplTeam={importFplTeam}
            importFromSavedFplTeam={importFromSavedFplTeam}
            getPositionClass={getPositionClass}
            renderTransfersPitch={renderTransfersPitch}
            renderBeforeAfterPitch={renderBeforeAfterPitch}
            parseFormation={parseFormation}
            API_BASE={API_BASE}
            resultsSectionRef={resultsSectionRef}
            squadSectionRef={squadSectionRef}
          />
        )}

        {/* Wildcard Tab */}
        {activeTab === 'wildcard' && (
          <WildcardTab
            gameweek={gameweek?.id ?? null}
            trajectory={wildcardTrajectory}
            loading={loadingWildcard}
            onGenerate={handleWildcardGenerate}
          />
        )}

        {/* Free Hit of the Week Tab */}
        {activeTab === 'selected_teams' && (
          <SelectedTeamsTab
            selectedTeams={selectedTeams}
            selectedGameweekTab={selectedGameweekTab}
            setSelectedGameweekTab={setSelectedGameweekTab}
            updateDailySnapshot={updateDailySnapshot}
            isTaskRunning={isTaskRunning}
            snapshotUpdateMessage={snapshotUpdateMessage}
            getPositionClass={getPositionClass}
            renderPitchFormation={renderPitchFormation}
          />
        )}

        {/* Top Picks Tab */}
        {activeTab === 'picks' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
            {Object.entries(topPicks).map(([position, players]) => (
              <div key={position} className="card">
                <div className="card-header capitalize">
                  <Star className="w-5 h-5 text-yellow-400" />
                  Top {position}
                </div>
                <div className="space-y-2">
                  {players.map((player: any, i: number) => (
                    <div key={player.id} className="p-3 bg-[#0f0f1a] rounded-lg">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className="text-gray-500 font-mono w-4">{i + 1}</span>
                          <div>
                            <div className="font-medium">{player.name}</div>
                            <div className="text-sm text-gray-400">{player.team} • £{player.price}m</div>
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-[#00ff87] font-mono font-semibold">{player.predicted_points?.toFixed(1) ?? '0.0'}</div>
                          <span className={`text-xs px-1.5 py-0.5 rounded ${
                            player.difficulty <= 2 ? 'bg-green-500/20 text-green-400' :
                            player.difficulty <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
                            'bg-red-500/20 text-red-400'
                          }`}>
                            {player.is_home ? 'vs' : '@'} {player.opponent}
                          </span>
                        </div>
                      </div>
                      <div className="text-xs text-gray-500 mt-2 pl-7">{player.reason}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Triple Captain Tab */}
        {activeTab === 'triple-captain' && (
          <TripleCaptainTab
            tripleCaptainRecs={tripleCaptainRecs}
            selectedTcGameweekTab={selectedTcGameweekTab}
            setSelectedTcGameweekTab={setSelectedTcGameweekTab}
            loadingTripleCaptain={loadingTripleCaptain}
          />
        )}

        {activeTab === 'differentials' && (
          <div className="card">
            <div className="card-header">
              <Flame className="w-5 h-5 text-orange-400" />
              Differentials (Under 10% Owned)
            </div>
            <p className="text-gray-400 text-sm mb-4">
              Low-ownership players with high predicted points - great for climbing ranks!
            </p>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-gray-400 text-sm border-b border-[#2a2a4a]">
                    <th className="pb-3">#</th>
                    <th className="pb-3">Player</th>
                    <th className="pb-3">Fixture</th>
                    <th className="pb-3">Pos</th>
                    <th className="pb-3 text-right">Price</th>
                    <th className="pb-3 text-right">Own%</th>
                    <th className="pb-3 text-right">Pts</th>
                    <th className="pb-3">Why</th>
                  </tr>
                </thead>
                <tbody>
                  {differentials.map((player: any, i: number) => (
                    <tr key={player.id} className="border-b border-[#2a2a4a]/50 hover:bg-[#1f1f3a] transition-colors">
                      <td className="py-3 text-gray-500 font-mono">{i + 1}</td>
                      <td className="py-3">
                        <span className="font-medium">{player.name}</span>
                        <span className="text-gray-500 text-xs ml-1">({player.team})</span>
                      </td>
                      <td className="py-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          player.difficulty <= 2 ? 'bg-green-500/20 text-green-400' :
                          player.difficulty <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
                          'bg-red-500/20 text-red-400'
                        }`}>
                          {player.is_home ? 'vs' : '@'} {player.opponent}
                        </span>
                      </td>
                      <td className="py-3">
                        <span className={`px-2 py-1 rounded text-xs font-medium border ${getPositionClass(player.position)}`}>
                          {player.position}
                        </span>
                      </td>
                      <td className="py-3 text-right font-mono text-sm">£{player.price}m</td>
                      <td className="py-3 text-right font-mono text-orange-400">{player.ownership}%</td>
                      <td className="py-3 text-right font-mono text-[#00ff87] font-semibold">{player.predicted_points?.toFixed(1) ?? '0.0'}</td>
                      <td className="py-3 text-xs text-gray-400 max-w-[150px]">{player.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tasks Tab */}
        {activeTab === 'tasks' && (
          <div className="space-y-6">
            <div className="card">
              <div className="card-header">
                <div className="flex items-center gap-2">
                  <Clock className="w-5 h-5 text-cyan-400" />
                  <span>Background Tasks</span>
                </div>
              </div>
              <p className="text-gray-400 text-sm mb-4">
                Track the progress of background tasks like squad updates and Triple Captain calculations.
              </p>

              {tasks.length === 0 ? (
                <div className="text-center py-12 text-gray-400">
                  <Clock className="w-12 h-12 mx-auto mb-4 opacity-50" />
                  <p>No active tasks</p>
                  <p className="text-xs mt-2">Tasks will appear here when you trigger background operations.</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {tasks.map((task) => (
                    <div
                      key={task.id}
                      className="bg-[#0f0f1a] rounded-lg border border-[#2a2a4a] p-4"
                    >
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            {task.status === 'running' && (
                              <Loader2 className="w-4 h-4 text-cyan-400 animate-spin" />
                            )}
                            {task.status === 'completed' && (
                              <CheckCircle2 className="w-4 h-4 text-green-400" />
                            )}
                            {task.status === 'failed' && (
                              <AlertCircle className="w-4 h-4 text-red-400" />
                            )}
                            {task.status === 'pending' && (
                              <Clock className="w-4 h-4 text-gray-400" />
                            )}
                            <h3 className="font-medium text-white">{task.title}</h3>
                          </div>
                          <p className="text-sm text-gray-400">{task.description}</p>
                        </div>
                        <div className="text-right">
                          <div className={`text-xs font-medium px-2 py-1 rounded ${
                            task.status === 'running' ? 'bg-cyan-500/20 text-cyan-400' :
                            task.status === 'completed' ? 'bg-green-500/20 text-green-400' :
                            task.status === 'failed' ? 'bg-red-500/20 text-red-400' :
                            'bg-gray-500/20 text-gray-400'
                          }`}>
                            {task.status === 'running' ? 'Running' :
                             task.status === 'completed' ? 'Completed' :
                             task.status === 'failed' ? 'Failed' :
                             'Pending'}
                          </div>
                        </div>
                      </div>

                      {/* Progress Bar */}
                      {(task.status === 'running' || task.status === 'pending') && (
                        <div className="mb-2">
                          <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
                            <span>Progress</span>
                            <span>{Math.round(task.progress)}%</span>
                          </div>
                          <div className="w-full bg-[#1a1a2e] rounded-full h-2 overflow-hidden">
                            <div
                              className="h-full bg-gradient-to-r from-cyan-500 to-cyan-400 transition-all duration-300 ease-out"
                              style={{ width: `${task.progress}%` }}
                            />
                          </div>
                        </div>
                      )}

                      {task.status === 'completed' && task.completedAt && (
                        <div className="text-xs text-gray-500 mt-2">
                          Completed {new Date(task.completedAt).toLocaleString('en-US', {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                        </div>
                      )}

                      {task.status === 'failed' && task.error && (
                        <div className="mt-2 p-2 bg-red-500/10 border border-red-500/30 rounded text-xs text-red-400">
                          {task.error}
                        </div>
                      )}

                      {task.status === 'completed' && (
                        <div className="mt-2 text-xs text-gray-500">
                          Started {new Date(task.createdAt).toLocaleString('en-US', {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      {/* Task Started Modal */}
      {taskStartedModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-[#1a1a2e] border border-[#2a2a4a] rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <div className="flex items-start gap-4">
              <div className="flex-shrink-0">
                <div className="w-12 h-12 bg-cyan-500/20 rounded-full flex items-center justify-center">
                  <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
                </div>
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-semibold text-white mb-2">
                  Task Started
                </h3>
                <p className="text-gray-300 text-sm mb-1">
                  <span className="font-medium">{taskStartedModal.title}</span> is now running in the background.
                </p>
                <p className="text-gray-400 text-xs mb-4">
                  You can track its progress in the <span className="text-cyan-400 font-medium">Tasks</span> tab.
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setActiveTab('tasks')
                      setTaskStartedModal(null)
                    }}
                    className="flex-1 px-4 py-2 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-400 rounded-lg border border-cyan-500/30 transition-colors text-sm font-medium"
                  >
                    View Tasks Tab
                  </button>
                  <button
                    onClick={() => setTaskStartedModal(null)}
                    className="px-4 py-2 bg-[#2a2a4a] hover:bg-[#3a3a5a] text-gray-300 rounded-lg transition-colors text-sm font-medium"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
              <button
                onClick={() => setTaskStartedModal(null)}
                className="text-gray-400 hover:text-white transition-colors flex-shrink-0"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Notification Container - Bottom Right */}
      <div className="fixed bottom-4 right-4 z-50 space-y-2 max-w-sm">
        {notifications.map((notification) => (
          <div
            key={notification.id}
            className={`p-4 rounded-lg border shadow-lg animate-in slide-in-from-right ${
              notification.type === 'success'
                ? 'bg-green-500/10 border-green-500/30 text-green-400'
                : 'bg-red-500/10 border-red-500/30 text-red-400'
            }`}
          >
            <div className="flex items-start gap-3">
              {notification.type === 'success' ? (
                <CheckCircle2 className="w-5 h-5 flex-shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
              )}
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm mb-1">{notification.title}</div>
                <div className="text-xs opacity-90">{notification.message}</div>
              </div>
              <button
                onClick={() => setNotifications(prev => prev.filter(n => n.id !== notification.id))}
                className="text-gray-400 hover:text-white transition-colors flex-shrink-0"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Footer */}
      <footer className="border-t border-[#2a2a4a] py-6 mt-12">
        <div className="max-w-6xl mx-auto px-6 text-center text-gray-500 text-sm">
          FPL Squad Suggester • AI-powered predictions • Not affiliated with Premier League
        </div>
      </footer>
      </div>
    </div>
  )
}

export default App
