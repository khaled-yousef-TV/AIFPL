/**
 * Central export for all API modules
 */

// Base client
export { API_BASE, apiRequest, apiFetch } from './client'

// Hermes
export {
  fetchHermesStatus,
  fetchSignals,
  startHermesRun,
  fetchHermesRun,
  fetchLatestHermesRun,
  fetchLatestAllHermesRuns,
  fetchActiveHermesRuns,
  fetchCalibration,
  fetchArchiveStatus,
  fetchBacktest,
} from './hermes'
export type {
  HermesStatus,
  HermesRun,
  HermesRunType,
  ActiveHermesRun,
  AgentReport,
  SignalsResponse,
  StartRunResponse,
  CalibrationResponse,
  BacktestSummary,
} from './hermes'

// Tasks
export { fetchTasks } from './tasks'
export type { TasksResponse, TaskDurationStats } from './tasks'
