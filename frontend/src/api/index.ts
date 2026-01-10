/**
 * Central export for all API modules
 * 
 * Usage: import { fetchGameweek, fetchTopPicks } from './api'
 */

// Base client
export { API_BASE, apiRequest, apiFetch } from './client'

// Gameweek & Selected Teams
export { 
  fetchGameweek, 
  fetchSelectedTeams, 
  fetchSelectedTeam 
} from './gameweek'
export type { SelectedTeamsResponse } from './gameweek'

// Predictions
export { 
  fetchTopPicks, 
  fetchDifferentials, 
  fetchPredictions 
} from './predictions'
export type { TopPicksResponse, DifferentialsResponse } from './predictions'

// Transfers
export { 
  fetchTransferSuggestions, 
  fetchWildcard, 
  importFplTeam 
} from './transfers'
export type { 
  TransferRequest, 
  TransferSuggestionsResponse, 
  WildcardResponse,
  ImportFplTeamResponse 
} from './transfers'

// Tasks
export { 
  fetchTasks, 
  createTask, 
  updateTask, 
  deleteTask,
  triggerDailySnapshot 
} from './tasks'
export type { TasksResponse, TaskResponse } from './tasks'

// Chips
export { 
  fetchTripleCaptain, 
  checkTripleCaptainStatus 
} from './chips'
export type { TripleCaptainCandidate, TripleCaptainResponse } from './chips'

// FPL Teams
export { 
  fetchSavedFplTeams, 
  saveFplTeam 
} from './fpl'
export type { FplTeamsResponse, SaveFplTeamResponse } from './fpl'

// Players
export { searchPlayers } from './players'
export type { PlayerSearchResult, PlayersSearchResponse } from './players'

