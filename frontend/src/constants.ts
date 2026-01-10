/**
 * Application-wide constants
 */

// Local Storage Keys
export const DRAFT_KEY = 'fpl_squad_draft_v1' // Local draft auto-save
export const TASKS_KEY = 'fpl_tasks_v1' // Persisting background tasks

// Position display order
export const POSITION_ORDER = ['GK', 'DEF', 'MID', 'FWD'] as const

// Formation configurations
export const FORMATIONS = {
  '3-4-3': { DEF: 3, MID: 4, FWD: 3 },
  '3-5-2': { DEF: 3, MID: 5, FWD: 2 },
  '4-3-3': { DEF: 4, MID: 3, FWD: 3 },
  '4-4-2': { DEF: 4, MID: 4, FWD: 2 },
  '4-5-1': { DEF: 4, MID: 5, FWD: 1 },
  '5-3-2': { DEF: 5, MID: 3, FWD: 2 },
  '5-4-1': { DEF: 5, MID: 4, FWD: 1 },
} as const

// Squad constraints
export const SQUAD_SIZE = 15
export const STARTING_XI_SIZE = 11
export const MAX_PLAYERS_PER_TEAM = 3

// Position colors for UI
export const POSITION_COLORS = {
  GK: {
    bg: 'bg-yellow-500/20',
    border: 'border-yellow-500/50',
    text: 'text-yellow-400',
  },
  DEF: {
    bg: 'bg-green-500/20',
    border: 'border-green-500/50',
    text: 'text-green-400',
  },
  MID: {
    bg: 'bg-blue-500/20',
    border: 'border-blue-500/50',
    text: 'text-blue-400',
  },
  FWD: {
    bg: 'bg-red-500/20',
    border: 'border-red-500/50',
    text: 'text-red-400',
  },
} as const

// Default budget
export const DEFAULT_BUDGET = 100.0

// Refresh intervals (in milliseconds)
export const TASK_POLL_INTERVAL = 3000
export const NOTIFICATION_DURATION = 5000

