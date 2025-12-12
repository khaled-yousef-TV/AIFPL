/**
 * API Client for FPL Agent Backend
 */

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}/api${endpoint}`
  
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

export const api = {
  // Auth
  async login(email: string, password: string) {
    return request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  },
  
  async logout() {
    return request('/auth/logout', { method: 'POST' })
  },
  
  async getAuthStatus() {
    return request<{ authenticated: boolean; team_id: number | null }>('/auth/status')
  },
  
  // Team
  async getCurrentTeam() {
    return request<{ players: any[]; captain_id: number }>('/team/current')
  },
  
  async getTeamInfo() {
    return request<any>('/team/info')
  },
  
  // Predictions
  async getPredictions(topN: number = 50) {
    return request<{ predictions: any[] }>(`/predictions?top_n=${topN}`)
  },
  
  // Recommendations
  async getCaptainRecommendation() {
    return request<any>('/recommendations/captain')
  },
  
  async getTransferRecommendations() {
    return request<any>('/recommendations/transfers')
  },
  
  async getDifferentials() {
    return request<{ differentials: any[] }>('/recommendations/differentials')
  },
  
  // Actions
  async setLineup(data: {
    starting_ids: number[]
    bench_ids: number[]
    captain_id: number
    vice_captain_id: number
  }) {
    return request('/actions/set-lineup', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },
  
  // FPL Data
  async getGameweek() {
    return request<{
      current: { id: number; name: string } | null
      next: { id: number; name: string; deadline: string } | null
    }>('/fpl/gameweek')
  },
  
  async getPlayers(position?: number, topN: number = 100) {
    let url = `/fpl/players?top_n=${topN}`
    if (position) url += `&position=${position}`
    return request<{ players: any[] }>(url)
  },
  
  // History
  async getDecisionHistory(limit: number = 20) {
    return request<{ decisions: any[] }>(`/history/decisions?limit=${limit}`)
  },
  
  async getPerformanceHistory() {
    return request<{ history: any[] }>('/history/performance')
  },
  
  // Settings
  async getSettings() {
    return request<any>('/settings')
  },
  
  async updateSettings(settings: {
    auto_execute?: boolean
    differential_mode?: boolean
    notification_email?: string
  }) {
    return request('/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    })
  },
}

