/**
 * Base API Client for FPL Dashboard
 * 
 * All API modules should use this base client for consistency.
 */

// In production (GitHub Pages) set this to your hosted backend
// In local dev it defaults to http://localhost:8001
export const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8001'

/**
 * Base fetch wrapper with error handling
 */
export async function apiRequest<T>(
  endpoint: string, 
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`
  
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })
  
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }))
    throw new Error(error.detail || `HTTP ${response.status}`)
  }
  
  return response.json()
}

/**
 * Simple fetch that returns response directly (for cases needing response object)
 */
export async function apiFetch(
  endpoint: string, 
  options: RequestInit = {}
): Promise<Response> {
  const url = `${API_BASE}${endpoint}`
  
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })
}
