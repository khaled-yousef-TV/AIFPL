import { useState, useEffect, useCallback } from 'react'
import { 
  Users, TrendingUp, RefreshCw, Zap, Award, 
  ChevronRight, Star, Target, Flame, AlertTriangle, Plane,
  ArrowRightLeft, Search, Plus, X, Trash2, Trophy
} from 'lucide-react'

// FPL-themed logo component
const FPLLogo = ({ className }: { className?: string }) => (
  <svg 
    viewBox="0 0 40 40" 
    className={className}
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    {/* Trophy base */}
    <path 
      d="M20 8C18.5 8 17.5 9 17.5 10.5V12H14C13.4 12 13 12.4 13 13V17C13 19.8 15.2 22 18 22H22C24.8 22 27 19.8 27 17V13C27 12.4 26.6 12 26 12H22.5V10.5C22.5 9 21.5 8 20 8Z" 
      fill="currentColor"
      className="text-[#FFD700]"
    />
    {/* Trophy cup */}
    <path 
      d="M20 24C16.7 24 14 21.3 14 18V16H26V18C26 21.3 23.3 24 20 24Z" 
      fill="currentColor"
      className="text-[#FFA500]"
    />
    {/* Star on trophy */}
    <path 
      d="M20 14L20.9 16.5L23.5 16.8L21.5 18.5L22.2 21L20 19.5L17.8 21L18.5 18.5L16.5 16.8L19.1 16.5L20 14Z" 
      fill="currentColor"
      className="text-[#FFD700]"
    />
    {/* FPL letters */}
    <text 
      x="20" 
      y="32" 
      textAnchor="middle" 
      className="text-[8px] font-bold fill-white"
      fontFamily="Arial, sans-serif"
      fontWeight="bold"
    >
      FPL
    </text>
  </svg>
)

// In production (GitHub Pages) set this to your hosted backend, e.g. https://api.fplai.nl
// In local dev it defaults to http://localhost:8001
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8001'

// Types
interface Player {
  id: number
  name: string
  full_name?: string
  team: string
  position: string
  position_id?: number
  price: number
  predicted?: number
  predicted_points?: number
  form?: number
  total_points?: number
  ownership?: number
  is_captain?: boolean
  is_vice_captain?: boolean
  rotation_risk?: string
  european_comp?: string
  opponent?: string
  difficulty?: number
  is_home?: boolean
  reason?: string
}

interface SuggestedSquad {
  gameweek: number
  formation: string
  starting_xi: Player[]
  bench: Player[]
  captain: { id: number; name: string; predicted: number }
  vice_captain: { id: number; name: string; predicted: number }
  total_cost: number
  remaining_budget: number
  predicted_points: number
}

interface GameWeekInfo {
  current?: { id: number; name: string }
  next?: { id: number; name: string; deadline: string }
}

interface SquadPlayer {
  id: number
  name: string
  position: string
  // IMPORTANT: For "My Transfers" this should be the user's SELLING price.
  // Search results provide current price, which may differ from selling price.
  price: number
  team?: string
  rotation_risk?: string
  european_comp?: string
}

interface TransferSuggestion {
  out: any
  in: any
  cost: number
  points_gain: number
  priority_score: number
  reason: string
  all_reasons: string[]
  teammate_comparison?: {
    team?: string
    position?: string
    why?: string
    chosen?: any
    alternatives?: any[]
  }
  type?: 'hold' | 'transfer'
  why?: string[]
  best_net_gain?: number | null
  hit_cost?: number
  best_alternative?: any
}

type SavedSquad = {
  id: string
  name: string
  updatedAt: number
  squad: SquadPlayer[]
  bank: number
  freeTransfers: number
}

function App() {
  const [loading, setLoading] = useState(true)
  const [squad, setSquad] = useState<SuggestedSquad | null>(null)
  const [squadHeuristic, setSquadHeuristic] = useState<SuggestedSquad | null>(null)
  const [squadForm, setSquadForm] = useState<SuggestedSquad | null>(null)
  const [squadFixture, setSquadFixture] = useState<SuggestedSquad | null>(null)
  const [topPicks, setTopPicks] = useState<Record<string, Player[]>>({})
  const [differentials, setDifferentials] = useState<Player[]>([])
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [activeTab, setActiveTab] = useState('transfers')
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [savingSquad, setSavingSquad] = useState(false)
  const [loadingSquad, setLoadingSquad] = useState(false)
  const [updatingSquad, setUpdatingSquad] = useState(false)
  const [deletingSquad, setDeletingSquad] = useState(false)
  
  // Transfer tab state
  const [mySquad, setMySquad] = useState<SquadPlayer[]>([])
  const [bank, setBank] = useState(0)
  const [freeTransfers, setFreeTransfers] = useState(1)
  const [transferSuggestions, setTransferSuggestions] = useState<TransferSuggestion[]>([])
  const [squadAnalysis, setSquadAnalysis] = useState<any[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Player[]>([])
  const [searchPosition, setSearchPosition] = useState<string>('')
  const [transferLoading, setTransferLoading] = useState(false)

  // Saved squads (persist between weeks) - now server-side
  const [savedSquads, setSavedSquads] = useState<SavedSquad[]>([])
  const [selectedSavedName, setSelectedSavedName] = useState<string>('')
  const [saveName, setSaveName] = useState<string>('My Squad')
  const [loadingSavedSquads, setLoadingSavedSquads] = useState(false)
  
  // Removed: selectedSavedId (old localStorage-based code)

  // Selected teams (suggested squads for each gameweek) - fetched from API
  type SelectedTeam = {
    gameweek: number
    squad: SuggestedSquad
    saved_at: string
  }
  const [selectedTeams, setSelectedTeams] = useState<Record<number, SelectedTeam>>({})
  const [loadingSelectedTeams, setLoadingSelectedTeams] = useState(false)
  const [selectedGameweekTab, setSelectedGameweekTab] = useState<number | null>(null)

  const DRAFT_KEY = 'fpl_squad_draft_v1' // Still used for local draft auto-save

  useEffect(() => {
    loadInitial()
  }, [])

  // Load saved squads from API on mount
  const loadSavedSquads = async () => {
    setLoadingSavedSquads(true)
    try {
      const res = await fetch(`${API_BASE}/api/saved-squads`).then(r => r.json())
      if (res.squads && Array.isArray(res.squads)) {
        // Map API response to frontend format
        const mapped = res.squads.map((s: any) => {
          // API squad_data contains { squad, bank, freeTransfers }
          const squadData = s.squad || {}
          return {
            id: s.name, // Use name as id for API compatibility
            name: s.name,
            updatedAt: s.updated_at ? new Date(s.updated_at).getTime() : new Date(s.saved_at).getTime(),
            squad: squadData.squad || [],
            bank: squadData.bank || 0,
            freeTransfers: squadData.freeTransfers || 1,
          }
        })
        setSavedSquads(mapped)
      }
    } catch (err) {
      console.error('Failed to load saved squads:', err)
    } finally {
      setLoadingSavedSquads(false)
    }
  }

  // Load saved squads and draft on mount
  useEffect(() => {
    loadSavedSquads()

    // Load draft squad from localStorage (still local, not synced)
    try {
      const rawDraft = localStorage.getItem(DRAFT_KEY)
      if (rawDraft) {
        const d = JSON.parse(rawDraft)
        if (d && Array.isArray(d.squad)) {
          setMySquad(d.squad)
          if (typeof d.bank === 'number') setBank(d.bank)
          if (typeof d.freeTransfers === 'number') setFreeTransfers(d.freeTransfers)
        }
      }
    } catch {}
  }, [])

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

  const loadSavedSquad = async () => {
    if (!selectedSavedName) return
    
    setLoadingSquad(true)
    try {
      const res = await fetch(`${API_BASE}/api/saved-squads/${encodeURIComponent(selectedSavedName)}`)
      if (!res.ok) {
        throw new Error('Failed to load saved squad')
      }
      const data = await res.json()
      const squadData = data.squad || {}
      
      setMySquad(squadData.squad || [])
      setBank(squadData.bank ?? 0)
      setFreeTransfers(squadData.freeTransfers ?? 1)
      setSaveName(data.name || 'My Squad')
    } catch (err) {
      console.error('Failed to load saved squad:', err)
      alert('Failed to load saved squad. Please try again.')
    } finally {
      setLoadingSquad(false)
    }
  }

  const saveOrUpdateSquad = async (mode: 'update' | 'new') => {
    const name = (saveName || 'My Squad').trim()
    if (!name) {
      alert('Please enter a squad name')
      return
    }
    
    // Prepare squad data to save
    const squadData = {
      squad: mySquad,
      bank: bank,
      freeTransfers: freeTransfers
    }

    if (mode === 'update' && selectedSavedName) {
      if (selectedSavedName !== name) {
        alert('Cannot change squad name. Please create a new squad with a different name.')
        return
      }
      
      setUpdatingSquad(true)
      try {
        const res = await fetch(`${API_BASE}/api/saved-squads/${encodeURIComponent(name)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, squad: squadData })
        })
        
        if (!res.ok) {
          const error = await res.json().catch(() => ({ detail: 'Failed to update squad' }))
          throw new Error(error.detail || 'Failed to update squad')
        }
        
        // Reload saved squads to get updated data
        await loadSavedSquads()
      } catch (err: any) {
        console.error('Failed to update squad:', err)
        alert(err.message || 'Failed to update squad. Please try again.')
      } finally {
        setUpdatingSquad(false)
      }
      return
    }

    // Create new squad
    setSavingSquad(true)
    try {
      const res = await fetch(`${API_BASE}/api/saved-squads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, squad: squadData })
      })
      
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Failed to save squad' }))
        throw new Error(error.detail || 'Failed to save squad')
      }
      
      // Reload saved squads and select the newly created one
      await loadSavedSquads()
      setSelectedSavedName(name)
      setSaveName(name)
    } catch (err: any) {
      console.error('Failed to save squad:', err)
      alert(err.message || 'Failed to save squad. Please try again.')
    } finally {
      setSavingSquad(false)
    }
  }

  const deleteSavedSquad = async () => {
    if (!selectedSavedName) return
    
    if (!confirm(`Are you sure you want to delete "${selectedSavedName}"?`)) {
      return
    }
    
    setDeletingSquad(true)
    try {
      const res = await fetch(`${API_BASE}/api/saved-squads/${encodeURIComponent(selectedSavedName)}`, {
        method: 'DELETE'
      })
      
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Failed to delete squad' }))
        throw new Error(error.detail || 'Failed to delete squad')
      }
      
      // Reload saved squads and clear selection
      await loadSavedSquads()
      setSelectedSavedName('')
      setSaveName('My Squad')
    } catch (err: any) {
      console.error('Failed to delete squad:', err)
      alert(err.message || 'Failed to delete squad. Please try again.')
    } finally {
      setDeletingSquad(false)
    }
  }

  const loadInitial = async () => {
    // Only load lightweight header data on boot (keeps My Transfers instant).
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

  const ensureSquadLoaded = async (method: 'combined' | 'heuristic' | 'form' | 'fixture') => {
    const hasData =
      method === 'combined' ? squad :
      method === 'heuristic' ? squadHeuristic :
      method === 'form' ? squadForm :
      squadFixture
    if (hasData) return

    try {
      const res = await fetch(`${API_BASE}/api/suggested-squad?method=${method}`).then(r => r.json())
      if (method === 'combined') setSquad(res)
      if (method === 'heuristic') setSquadHeuristic(res)
      if (method === 'form') setSquadForm(res)
      if (method === 'fixture') setSquadFixture(res)
    } catch (err) {
      console.error('Squad load error:', err)
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

  useEffect(() => {
    // Lazy-load heavy tabs only when the user opens them
    if (activeTab === 'squad_combined') ensureSquadLoaded('combined')
    if (activeTab === 'squad_heuristic') ensureSquadLoaded('heuristic')
    if (activeTab === 'squad_form') ensureSquadLoaded('form')
    if (activeTab === 'squad_fixture') ensureSquadLoaded('fixture')
    if (activeTab === 'picks') ensurePicksLoaded()
    if (activeTab === 'differentials') ensureDifferentialsLoaded()
  }, [activeTab])

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
      if (activeTab === 'squad_combined') {
        setSquad(null)
        const res = await fetch(`${API_BASE}/api/suggested-squad?method=combined`).then(r => r.json())
        setSquad(res)
      } else if (activeTab === 'squad_heuristic') {
        setSquadHeuristic(null)
        const res = await fetch(`${API_BASE}/api/suggested-squad?method=heuristic`).then(r => r.json())
        setSquadHeuristic(res)
      } else if (activeTab === 'squad_form') {
        setSquadForm(null)
        const res = await fetch(`${API_BASE}/api/suggested-squad?method=form`).then(r => r.json())
        setSquadForm(res)
      } else if (activeTab === 'squad_fixture') {
        setSquadFixture(null)
        const res = await fetch(`${API_BASE}/api/suggested-squad?method=fixture`).then(r => r.json())
        setSquadFixture(res)
      } else if (activeTab === 'picks') {
        setTopPicks({})
        const res = await fetch(`${API_BASE}/api/top-picks`).then(r => r.json())
        setTopPicks(res)
      } else if (activeTab === 'differentials') {
        setDifferentials([])
        const res = await fetch(`${API_BASE}/api/differentials`).then(r => r.json())
        setDifferentials(res.differentials || [])
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

  const addToSquad = (player: Player) => {
    if (mySquad.length >= 15) return
    if (mySquad.find(p => p.id === player.id)) return
    
    setMySquad([...mySquad, {
      id: player.id,
      name: player.name,
      position: player.position,
      // Default to CURRENT price; user can edit to their SELLING price.
      price: typeof player.price === 'number' ? Math.round(player.price * 10) / 10 : 0,
      team: player.team,
      rotation_risk: player.rotation_risk,
      european_comp: player.european_comp,
    }])
    setSearchQuery('')
    setSearchResults([])
  }

  const removeFromSquad = (playerId: number) => {
    setMySquad(mySquad.filter(p => p.id !== playerId))
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
      const suggestionsLimit = Math.max(3, Number.isFinite(freeTransfers) ? freeTransfers : 3)
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

  const isSquadTab = activeTab.startsWith('squad_')
  const currentSquad: SuggestedSquad | null =
    activeTab === 'squad_combined' ? squad :
    activeTab === 'squad_heuristic' ? squadHeuristic :
    activeTab === 'squad_form' ? squadForm :
    activeTab === 'squad_fixture' ? squadFixture :
    null

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
    <div className="min-h-screen bg-[#0f0f1a] text-white">
      {/* Header */}
      <header className="bg-[#1a1a2e] border-b border-[#2a2a4a] px-4 sm:px-6 py-3 sm:py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
            <div className="w-12 h-12 sm:w-14 sm:h-14 bg-gradient-to-br from-[#38003c] to-[#00ff87] rounded-lg flex items-center justify-center flex-shrink-0 shadow-lg border border-[#00ff87]/20">
              <FPLLogo className="w-8 h-8 sm:w-10 sm:h-10" />
            </div>
            <div className="min-w-0">
              <h1 className="font-bold text-sm sm:text-lg truncate">FPL Squad Suggester</h1>
              <p className="text-[10px] sm:text-xs text-gray-400 truncate">
                {gameweek?.next ? `GW${gameweek.next.id} • ${formatDeadline(gameweek.next.deadline)}` : 'Loading...'}
              </p>
            </div>
          </div>
          
          <button 
            onClick={refresh} 
            disabled={refreshing}
            className="btn btn-secondary flex items-center gap-1 sm:gap-2 text-xs sm:text-base px-2 sm:px-4 py-1.5 sm:py-2 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
          >
            <RefreshCw className={`w-3 h-3 sm:w-4 sm:h-4 ${refreshing ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">{refreshing ? 'Refreshing...' : 'Refresh'}</span>
          </button>
        </div>
      </header>

      {/* Navigation */}
      <nav className="bg-[#1a1a2e]/50 border-b border-[#2a2a4a] px-4 sm:px-6">
        <div className="max-w-6xl mx-auto overflow-x-auto scrollbar-hide">
          <div className="flex gap-1 min-w-max">
            {[
              { id: 'transfers', icon: ArrowRightLeft, label: 'My Transfers', shortLabel: 'Transfers' },
              { id: 'selected_teams', icon: Trophy, label: 'Selected Teams', shortLabel: 'Selected' },
              { id: 'squad_combined', icon: Users, label: 'Squad • Combined', shortLabel: 'Combined' },
              { id: 'squad_heuristic', icon: Zap, label: 'Squad • Heuristic', shortLabel: 'Heuristic' },
              { id: 'squad_form', icon: TrendingUp, label: 'Squad • Form', shortLabel: 'Form' },
              { id: 'squad_fixture', icon: Target, label: 'Squad • Fixture', shortLabel: 'Fixture' },
              { id: 'picks', icon: Star, label: 'Top Picks', shortLabel: 'Picks' },
              { id: 'differentials', icon: Target, label: 'Differentials', shortLabel: 'Diffs' },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 sm:py-3 border-b-2 transition-colors whitespace-nowrap ${
                  activeTab === tab.id 
                    ? 'border-[#00ff87] text-white' 
                    : 'border-transparent text-gray-400 hover:text-white'
                }`}
              >
                <tab.icon className="w-3 h-3 sm:w-4 sm:h-4 flex-shrink-0" />
                <span className="text-xs sm:text-sm">
                  <span className="sm:hidden">{tab.shortLabel}</span>
                  <span className="hidden sm:inline">{tab.label}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-6xl mx-auto p-4 sm:p-6">
        
        {/* Squad Tabs */}
        {isSquadTab && !currentSquad && (
          <div className="text-center text-gray-400 py-8">Loading squad...</div>
        )}

        {isSquadTab && currentSquad && (
          <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 sm:grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Method</div>
                <div className="text-lg font-bold text-[#00ff87]">{(currentSquad as any).method || 'Combined'}</div>
              </div>
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Formation</div>
                <div className="text-2xl font-bold text-[#00ff87]">{currentSquad.formation}</div>
              </div>
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Predicted Points</div>
                <div className="text-2xl font-bold text-[#00ff87]">{(currentSquad.predicted_points ?? 0).toFixed(1)}</div>
              </div>
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Squad Cost</div>
                <div className="text-2xl font-bold">£{currentSquad.total_cost}m</div>
              </div>
            </div>

            {/* European Rotation Notice */}
            <div className="bg-[#1a1a2e]/50 border border-[#2a2a4a] rounded-lg p-4">
              <div className="flex items-start gap-3">
                <Plane className="w-5 h-5 text-blue-400 mt-0.5" />
                <div>
                  <h3 className="font-medium text-sm mb-1">European Rotation Risk</h3>
                  <p className="text-xs text-gray-400 mb-2">
                    Teams in UCL/UEL/UECL may rotate players, especially for easier league games.
                  </p>
                  <div className="flex flex-wrap gap-3 text-xs">
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-orange-500"></span>
                      <span className="text-orange-400">High risk</span>
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-yellow-500"></span>
                      <span className="text-yellow-400">Medium risk</span>
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-blue-500"></span>
                      <span className="text-blue-400">In Europe (low risk)</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Captain Pick */}
            <div className="card">
              <div className="card-header">
                <Award className="w-5 h-5 text-yellow-400" />
                Captain Pick
              </div>
              <div className="flex flex-col sm:flex-row gap-3 sm:gap-4">
                <div className="flex-1 p-4 bg-yellow-500/10 rounded-lg border border-yellow-500/30">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-yellow-400 text-lg font-bold">©</span>
                    <span className="font-semibold text-lg">{currentSquad.captain.name}</span>
                  </div>
                  <span className="text-gray-400">Predicted: <span className="text-[#00ff87] font-mono">{(currentSquad.captain.predicted ?? 0).toFixed(1)} × 2 = {((currentSquad.captain.predicted ?? 0) * 2).toFixed(1)}</span></span>
                </div>
                <div className="flex-1 p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-gray-400">V</span>
                    <span className="font-medium">{currentSquad.vice_captain.name}</span>
                  </div>
                  <span className="text-gray-400">Predicted: <span className="font-mono">{(currentSquad.vice_captain.predicted ?? 0).toFixed(1)}</span></span>
                </div>
              </div>
            </div>

            {/* Starting XI */}
            <div className="card">
              <div className="card-header">
                <Users className="w-5 h-5 text-[#00ff87]" />
                Starting XI
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-gray-400 text-sm border-b border-[#2a2a4a]">
                      <th className="pb-3 w-8"></th>
                      <th className="pb-3">Player</th>
                      <th className="pb-3">Fixture</th>
                      <th className="pb-3">Pos</th>
                      <th className="pb-3 text-right">Price</th>
                      <th className="pb-3 text-right">Pts</th>
                      <th className="pb-3">Selection Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {currentSquad.starting_xi.map((player: any, i) => (
                      <tr key={player.id} className={`border-b border-[#2a2a4a]/50 hover:bg-[#1f1f3a] transition-colors ${
                        player.rotation_risk === 'high' ? 'bg-orange-500/5' : ''
                      }`}>
                        <td className="py-3">
                          {player.is_captain && <span className="text-yellow-400 font-bold">©</span>}
                          {player.is_vice_captain && <span className="text-gray-400">V</span>}
                        </td>
                        <td className="py-3">
                          <div className="flex items-center gap-2">
                            <div>
                              <span className="font-medium">{player.name}</span>
                              <span className="text-gray-500 text-xs ml-1">({player.team})</span>
                            </div>
                            {player.european_comp && (
                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                player.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
                                player.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
                                'bg-blue-500/20 text-blue-400'
                              }`}>
                                {player.european_comp}
                              </span>
                            )}
                          </div>
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
                        <td className="py-3 text-right font-mono text-[#00ff87] font-semibold">{player.predicted?.toFixed(1) ?? '0.0'}</td>
                        <td className="py-3 text-xs text-gray-400 max-w-[220px]">{player.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Bench */}
            <div className="card">
              <div className="card-header">
                <ChevronRight className="w-5 h-5 text-gray-400" />
                Bench
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                {currentSquad.bench.map((player: any, i) => (
                  <div key={player.id} className={`p-3 bg-[#0f0f1a] rounded-lg border ${
                    player.rotation_risk === 'high' ? 'border-orange-500/50' : 'border-[#2a2a4a]'
                  }`}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getPositionClass(player.position)}`}>
                          {player.position}
                        </span>
                        {player.european_comp && (
                          <span className={`px-1 py-0.5 rounded text-[10px] font-bold ${
                            player.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
                            player.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
                            'bg-blue-500/20 text-blue-400'
                          }`}>
                            {player.european_comp}
                          </span>
                        )}
                      </div>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        player.difficulty <= 2 ? 'bg-green-500/20 text-green-400' :
                        player.difficulty <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
                        'bg-red-500/20 text-red-400'
                      }`}>
                        {player.is_home ? 'vs' : '@'} {player.opponent}
                      </span>
                    </div>
                    <div className="font-medium">{player.name}</div>
                    <div className="text-sm text-gray-400">{player.team} • £{player.price}m</div>
                    <div className="text-sm text-[#00ff87] font-mono mt-1">{player.predicted?.toFixed(1) ?? '0.0'} pts</div>
                    <div className="text-xs text-gray-500 mt-1">{player.reason}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* My Transfers Tab */}
        {activeTab === 'transfers' && (
          <div className="space-y-6">
            {/* Instructions */}
            <div className="card">
              <div className="card-header">
                <ArrowRightLeft className="w-5 h-5 text-[#00ff87]" />
                Transfer Suggestions
              </div>
              <p className="text-gray-400 text-sm mb-4">
                Add your current squad below and get AI-powered transfer suggestions considering both short-term (next GW) and long-term (next 5 GWs) fixtures.
              </p>
              
              {/* Saved squads */}
              <div className="mt-4 p-3 sm:p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                <div className="space-y-3">
                  <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 sm:gap-3">
                    <div className="flex-1 flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                      <span className="text-xs text-gray-400 whitespace-nowrap">Saved squads</span>
                      <select
                        value={selectedSavedName}
                        onChange={(e) => setSelectedSavedName(e.target.value)}
                        disabled={loadingSavedSquads}
                        className="flex-1 px-3 py-1.5 sm:py-1 bg-[#0b0b14] border border-[#2a2a4a] rounded text-sm focus:border-[#00ff87] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <option value="">— Select —</option>
                        {savedSquads.map(s => (
                          <option key={s.id} value={s.name}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button 
                        onClick={loadSavedSquad} 
                        disabled={!selectedSavedName || loadingSquad || savingSquad || updatingSquad || deletingSquad}
                        className="btn btn-secondary text-xs sm:text-sm flex-1 sm:flex-none disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
                      >
                        {loadingSquad ? (
                          <>
                            <RefreshCw className="w-3 h-3 animate-spin" />
                            <span className="hidden sm:inline">Loading...</span>
                          </>
                        ) : (
                          'Load'
                        )}
                      </button>
                      <button 
                        onClick={() => saveOrUpdateSquad('update')} 
                        disabled={!selectedSavedName || loadingSquad || savingSquad || updatingSquad || deletingSquad}
                        className="btn btn-secondary text-xs sm:text-sm flex-1 sm:flex-none disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
                      >
                        {updatingSquad ? (
                          <>
                            <RefreshCw className="w-3 h-3 animate-spin" />
                            <span className="hidden sm:inline">Updating...</span>
                          </>
                        ) : (
                          'Update'
                        )}
                      </button>
                      <button 
                        onClick={() => saveOrUpdateSquad('new')} 
                        disabled={loadingSquad || savingSquad || updatingSquad || deletingSquad}
                        className="btn btn-secondary text-xs sm:text-sm flex-1 sm:flex-none disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
                      >
                        {savingSquad ? (
                          <>
                            <RefreshCw className="w-3 h-3 animate-spin" />
                            <span className="hidden sm:inline">Saving...</span>
                          </>
                        ) : (
                          'Save'
                        )}
                      </button>
                      <button 
                        onClick={deleteSavedSquad} 
                        disabled={!selectedSavedName || loadingSquad || savingSquad || updatingSquad || deletingSquad}
                        className="btn btn-secondary text-xs sm:text-sm flex-1 sm:flex-none disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
                      >
                        {deletingSquad ? (
                          <>
                            <RefreshCw className="w-3 h-3 animate-spin" />
                            <span className="hidden sm:inline">Deleting...</span>
                          </>
                        ) : (
                          'Delete'
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                    <span className="text-xs text-gray-400 whitespace-nowrap">Name</span>
                    <input
                      value={saveName}
                      onChange={(e) => setSaveName(e.target.value)}
                      disabled={!!selectedSavedName}
                      className="flex-1 px-3 py-1.5 sm:py-1 bg-[#0b0b14] border border-[#2a2a4a] rounded text-sm focus:border-[#00ff87] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-[#080811]"
                      placeholder="My Squad"
                    />
                  </div>
                </div>
                <div className="text-[10px] sm:text-[11px] text-gray-500 mt-2">
                  Your current squad is auto-saved locally and saved squads are synced across devices.
                </div>
              </div>

              {/* Squad Input */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
                {/* Search & Add */}
                <div>
                  <h3 className="font-medium mb-3">Add Players to Squad</h3>
                  <div className="relative mb-4">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => {
                        setSearchQuery(e.target.value)
                        searchPlayers(e.target.value, searchPosition)
                      }}
                              placeholder="Search player name or team (e.g., Spurs / TOT)..."
                      className="w-full pl-10 pr-4 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg focus:border-[#00ff87] focus:outline-none"
                    />
                  </div>
                  
                  <div className="flex gap-2 mb-4">
                    {['', 'GK', 'DEF', 'MID', 'FWD'].map(pos => (
                      <button
                        key={pos}
                        onClick={() => {
                          setSearchPosition(pos)
                          // If the user hasn't typed anything, show cheapest options for that position.
                          // If they have typed 2+ chars, search by name.
                          searchPlayers(searchQuery, pos)
                        }}
                        className={`px-3 py-1 rounded text-sm ${
                          searchPosition === pos 
                            ? 'bg-[#00ff87] text-[#0f0f1a]' 
                            : 'bg-[#2a2a4a] text-gray-300 hover:bg-[#3a3a5a]'
                        }`}
                      >
                        {pos || 'All'}
                      </button>
                    ))}
                  </div>
                  
                  {/* Search Results */}
                  {searchResults.length > 0 && (
                    <div className="bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg max-h-60 overflow-y-auto">
                      {searchResults.map(player => (
                        <button
                          key={player.id}
                          onClick={() => addToSquad(player)}
                          disabled={mySquad.find(p => p.id === player.id) !== undefined}
                          className="w-full flex items-center justify-between p-3 hover:bg-[#1f1f3a] border-b border-[#2a2a4a] last:border-0 disabled:opacity-50"
                        >
                          <div className="flex items-center gap-3">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getPositionClass(player.position)}`}>
                              {player.position}
                            </span>
                            <div className="text-left">
                              <div className="font-medium">{player.name}</div>
                              <div className="text-xs text-gray-400">
                                <span>{player.team}</span>
                                {player.european_comp && (
                                  <span className={`ml-2 px-1 py-0.5 rounded text-[10px] font-bold ${
                                    player.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
                                    player.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
                                    'bg-blue-500/20 text-blue-400'
                                  }`}>
                                    {player.european_comp}
                                  </span>
                                )}
                                {typeof (player as any).minutes === 'number' && (
                                  <span className="text-gray-500"> • {(player as any).minutes}m</span>
                                )}
                                {(player as any).status && (player as any).status !== 'a' && (
                                  <span className="text-orange-400"> • {String((player as any).status).toUpperCase()}</span>
                                )}
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-mono">£{player.price}m</span>
                            <Plus className="w-4 h-4 text-[#00ff87]" />
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                
                {/* Current Squad */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="font-medium">Your Squad ({mySquad.length}/15)</h3>
                    {mySquad.length > 0 && (
                      <button 
                        onClick={() => setMySquad([])}
                        className="text-xs text-red-400 hover:text-red-300"
                      >
                        Clear All
                      </button>
                    )}
                  </div>
                  
                  <div className="space-y-2 max-h-80 overflow-y-auto">
                    <div className="text-xs text-gray-500 mb-2">
                      Prices from search are <span className="text-gray-300">current FPL prices</span>. Your in-game
                      <span className="text-gray-300"> selling price</span> can be different (e.g. you bought before a price rise).
                      Edit the £ value below to match your selling price.
                    </div>
                    {['GK', 'DEF', 'MID', 'FWD'].map(pos => {
                      const posPlayers = mySquad.filter(p => p.position === pos)
                      return (
                        <div key={pos}>
                          <div className="text-xs text-gray-500 mb-1">{pos} ({posPlayers.length})</div>
                          {posPlayers.map(player => (
                            <div key={player.id} className="flex items-center justify-between p-2 bg-[#0f0f1a] rounded mb-1">
                              <div className="flex items-center gap-2">
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${getPositionClass(player.position)}`}>
                                  {player.position}
                                </span>
                                <span className="text-sm">{player.name}</span>
                                <span className="text-xs text-gray-500">{player.team}</span>
                                {player.european_comp && (
                                  <span className={`px-1 py-0.5 rounded text-[10px] font-bold ${
                                    player.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
                                    player.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
                                    'bg-blue-500/20 text-blue-400'
                                  }`}>
                                    {player.european_comp}
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-2">
                                <div className="flex items-center gap-1 text-xs font-mono text-gray-400">
                                  <span>£</span>
                                  <input
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    value={Number.isFinite(player.price) ? player.price : 0}
                                    onChange={(e) => updateSquadPrice(player.id, parseFloat(e.target.value))}
                                    className="w-16 px-2 py-0.5 bg-[#0b0b14] border border-[#2a2a4a] rounded text-right focus:border-[#00ff87] focus:outline-none"
                                  />
                                  <span>m</span>
                                </div>
                                <button onClick={() => removeFromSquad(player.id)} className="text-red-400 hover:text-red-300">
                                  <X className="w-4 h-4" />
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      )
                    })}
                    
                    {mySquad.length === 0 && (
                      <div className="text-center text-gray-500 py-8">
                        Search and add players to your squad
                      </div>
                    )}
                  </div>
                </div>
              </div>
              
              {/* Bank & Free Transfers */}
              <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 mt-6 pt-4 border-t border-[#2a2a4a]">
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-400 whitespace-nowrap">Bank (£m)</label>
                  <input
                    type="number"
                    step="0.1"
                    value={bank}
                    onChange={(e) => setBank(parseFloat(e.target.value) || 0)}
                    className="w-24 px-3 py-1.5 sm:py-1 bg-[#0f0f1a] border border-[#2a2a4a] rounded focus:border-[#00ff87] focus:outline-none text-sm"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-400 whitespace-nowrap">Free Transfers</label>
                  <input
                    type="number"
                    min="0"
                    max="5"
                    value={freeTransfers}
                    onChange={(e) => setFreeTransfers(parseInt(e.target.value) || 1)}
                    className="w-20 sm:w-16 px-3 py-1.5 sm:py-1 bg-[#0f0f1a] border border-[#2a2a4a] rounded focus:border-[#00ff87] focus:outline-none text-sm"
                  />
                </div>
                <button
                  onClick={getTransferSuggestions}
                  disabled={mySquad.length < 11 || transferLoading}
                  className="btn btn-primary sm:ml-auto w-full sm:w-auto text-sm sm:text-base"
                >
                  {transferLoading ? (
                    <>
                      <RefreshCw className="w-4 h-4 animate-spin inline mr-2" />
                      Loading...
                    </>
                  ) : (
                    'Get Suggestions'
                  )}
                </button>
              </div>
            </div>
            
            {/* Transfer Suggestions */}
            {transferSuggestions.length > 0 && (
              <div className="card">
                <div className="card-header">
                  <TrendingUp className="w-5 h-5 text-[#00ff87]" />
                  Transfer Suggestions
                </div>
                
                <div className="space-y-4">
                  {transferSuggestions.map((suggestion, i) => (
                    (suggestion as any).type === 'hold' ? (
                      <div key={i} className="p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                        <div className="flex items-center justify-between mb-3">
                          <span className="text-lg font-bold text-[#00ff87]">✅ Hold</span>
                          <span className="px-2 py-1 rounded text-sm font-medium bg-blue-500/20 text-blue-300">
                            Save FT
                          </span>
                        </div>

                        <div className="text-sm text-gray-200 mb-2">
                          {(suggestion as any).reason || 'Hold / Save transfer'}
                        </div>
                        {Array.isArray((suggestion as any).why) && (suggestion as any).why.length > 0 && (
                          <ul className="text-xs text-gray-400 space-y-1 list-disc pl-4">
                            {(suggestion as any).why.slice(0, 4).map((w: string, idx: number) => (
                              <li key={idx}>{w}</li>
                            ))}
                          </ul>
                        )}

                        {(suggestion as any).best_alternative && (
                          <div className="mt-3 pt-3 border-t border-[#2a2a4a]">
                            <div className="text-[11px] text-gray-400 mb-2">Best alternative if you still want to move:</div>
                            <div className="flex items-center gap-2 text-sm">
                              <span className="text-red-400 font-semibold">OUT</span>
                              <span className="text-gray-200">{(suggestion as any).best_alternative.out?.name}</span>
                              <span className="text-gray-500">→</span>
                              <span className="text-[#00ff87] font-semibold">IN</span>
                              <span className="text-gray-200">{(suggestion as any).best_alternative.in?.name}</span>
                              <span className="text-gray-500 text-xs">
                                ({((suggestion as any).best_alternative.points_gain ?? 0)} pts, hit {(suggestion as any).hit_cost ?? 0})
                              </span>
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                    <div key={i} className="p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-lg font-bold text-[#00ff87]">#{i + 1}</span>
                        <span className={`px-2 py-1 rounded text-sm font-medium ${
                          suggestion.points_gain > 0 ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                        }`}>
                          {suggestion.points_gain > 0 ? '+' : ''}{suggestion.points_gain} pts
                        </span>
                      </div>
                      
                      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-4">
                        {/* Out */}
                        <div className="flex-1 p-3 bg-red-500/10 rounded-lg border border-red-500/30">
                          <div className="text-xs text-red-400 mb-1">Transfer Out</div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-sm sm:text-base">{suggestion.out.name}</span>
                            {suggestion.out.european_comp && (
                              <span className={`px-1 py-0.5 rounded text-[10px] font-bold ${
                                suggestion.out.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
                                suggestion.out.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
                                'bg-blue-500/20 text-blue-400'
                              }`}>
                                {suggestion.out.european_comp}
                              </span>
                            )}
                          </div>
                          <div className="text-xs sm:text-sm text-gray-400">{suggestion.out.team} • £{suggestion.out.price}m</div>
                          <div className="text-[10px] sm:text-xs text-gray-500 mt-1">
                            vs {suggestion.out.fixture} (FDR {suggestion.out.fixture_difficulty}) • Form: {suggestion.out.form}
                          </div>
                        </div>
                        
                        <ArrowRightLeft className="w-5 h-5 sm:w-6 sm:h-6 text-gray-500 mx-auto sm:mx-0 rotate-90 sm:rotate-0 flex-shrink-0" />
                        
                        {/* In */}
                        <div className="flex-1 p-3 bg-green-500/10 rounded-lg border border-green-500/30">
                          <div className="text-xs text-green-400 mb-1">Transfer In</div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-sm sm:text-base">{suggestion.in.name}</span>
                            {suggestion.in.european_comp && (
                              <span className="px-1 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-400">
                                {suggestion.in.european_comp}
                              </span>
                            )}
                          </div>
                          <div className="text-xs sm:text-sm text-gray-400">{suggestion.in.team} • £{suggestion.in.price}m</div>
                          <div className="text-[10px] sm:text-xs text-gray-500 mt-1">
                            vs {suggestion.in.fixture} (FDR {suggestion.in.fixture_difficulty}) • Form: {suggestion.in.form}
                          </div>
                        </div>
                      </div>
                      
                      {/* Reason */}
                      <div className="mt-3 pt-3 border-t border-[#2a2a4a]">
                        <div className="text-sm text-[#00ff87]">💡 {suggestion.reason}</div>
                        {suggestion.all_reasons.length > 1 && (
                          <div className="text-xs text-gray-500 mt-1">
                            Also: {suggestion.all_reasons.slice(1).join(' • ')}
                          </div>
                        )}
                        {suggestion.teammate_comparison?.why && (
                          <div className="mt-2 p-2 bg-[#1a1a2e]/40 border border-[#2a2a4a] rounded">
                            <div className="text-[11px] text-gray-400 mb-1">
                              Why {suggestion.in.name} over other {suggestion.teammate_comparison.team} {suggestion.teammate_comparison.position} options?
                            </div>
                            <div className="text-xs text-gray-300">
                              {suggestion.teammate_comparison.why}
                            </div>
                          </div>
                        )}
                        <div className="flex gap-4 mt-2 text-xs text-gray-400">
                          <span>Cost: {suggestion.cost > 0 ? '+' : ''}£{suggestion.cost}m</span>
                          <span>5GW Avg FDR: {suggestion.out.avg_fixture_5gw} → {suggestion.in.avg_fixture_5gw}</span>
                        </div>
                      </div>
                    </div>
                    )
                  ))}
                </div>
              </div>
            )}
            
            {/* Squad Analysis */}
            {squadAnalysis.length > 0 && (
              <div className="card">
                <div className="card-header">
                  <Target className="w-5 h-5 text-yellow-400" />
                  Squad Analysis (sorted by priority to transfer out)
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-gray-400 border-b border-[#2a2a4a]">
                        <th className="pb-2">Player</th>
                        <th className="pb-2">Fixture</th>
                        <th className="pb-2 text-right">Pred</th>
                        <th className="pb-2 text-right">Form</th>
                        <th className="pb-2 text-right">5GW FDR</th>
                        <th className="pb-2 text-right">Keep Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {squadAnalysis.map((player: any) => (
                        <tr key={player.id} className={`border-b border-[#2a2a4a]/50 ${
                          player.keep_score < 3 ? 'bg-red-500/10' : ''
                        }`}>
                          <td className="py-2">
                            <span className="font-medium">{player.name}</span>
                            <span className="text-gray-500 text-xs ml-1">({player.team})</span>
                          </td>
                          <td className="py-2">
                            <span className={`px-1.5 py-0.5 rounded text-xs ${
                              player.fixture_difficulty <= 2 ? 'bg-green-500/20 text-green-400' :
                              player.fixture_difficulty <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
                              'bg-red-500/20 text-red-400'
                            }`}>
                              {player.fixture} ({player.fixture_difficulty})
                            </span>
                          </td>
                          <td className="py-2 text-right font-mono">{player.predicted?.toFixed(1) ?? '0.0'}</td>
                          <td className="py-2 text-right font-mono">{player.form}</td>
                          <td className="py-2 text-right font-mono">{player.avg_fixture_5gw}</td>
                          <td className={`py-2 text-right font-mono font-bold ${
                            player.keep_score < 3 ? 'text-red-400' : 
                            player.keep_score < 5 ? 'text-yellow-400' : 'text-green-400'
                          }`}>
                            {player.keep_score}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Selected Teams Tab */}
        {activeTab === 'selected_teams' && (() => {
          const sortedTeams = Object.values(selectedTeams).sort((a, b) => b.gameweek - a.gameweek)
          // Initialize selected gameweek tab if not set
          const selectedGameweek = selectedGameweekTab || (sortedTeams.length > 0 ? sortedTeams[0].gameweek : null)
          const currentTeam = selectedGameweek ? selectedTeams[selectedGameweek] : null

          return (
            <div className="space-y-6">
              <div className="card">
                <div className="card-header">
                  <Trophy className="w-5 h-5 text-[#00ff87]" />
                  Selected Teams
                </div>
                <p className="text-gray-400 text-sm mb-4">
                  View your saved suggested squads. Squads are automatically saved 30 minutes before each gameweek deadline.
                </p>

                {Object.keys(selectedTeams).length === 0 ? (
                  <div className="text-center py-8 text-gray-400">
                    <Trophy className="w-12 h-12 mx-auto mb-4 opacity-50" />
                    <p>No selected teams saved yet.</p>
                    <p className="text-xs mt-2">Squads are automatically saved 30 minutes before the gameweek deadline.</p>
                  </div>
                ) : (
                  <>
                    {/* Gameweek Tabs */}
                    <div className="border-b border-[#2a2a4a] overflow-x-auto scrollbar-hide">
                      <div className="flex gap-1 min-w-max">
                        {sortedTeams.map((team) => (
                          <button
                            key={team.gameweek}
                            onClick={() => setSelectedGameweekTab(team.gameweek)}
                            className={`px-4 py-2 border-b-2 transition-colors whitespace-nowrap ${
                              selectedGameweek === team.gameweek
                                ? 'border-[#00ff87] text-white'
                                : 'border-transparent text-gray-400 hover:text-white'
                            }`}
                          >
                            <span className="text-sm font-medium">GW{team.gameweek}</span>
                            <span className="text-xs text-gray-500 ml-2">
                              {new Date(team.saved_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Selected Gameweek Content */}
                    {currentTeam && (
                      <div className="bg-[#0f0f1a] rounded-lg border border-[#2a2a4a] p-4 sm:p-6">
                        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6">
                          <div>
                            <h3 className="text-xl font-semibold text-[#00ff87] mb-1">Gameweek {currentTeam.gameweek}</h3>
                            <p className="text-xs text-gray-400">
                              Saved {new Date(currentTeam.saved_at).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                hour: '2-digit',
                                minute: '2-digit'
                              })}
                            </p>
                          </div>
                          <div className="flex gap-4 mt-2 sm:mt-0">
                            <div className="text-right">
                              <div className="text-xs text-gray-400">Predicted Points</div>
                              <div className="text-lg font-mono font-semibold text-[#00ff87]">
                                {(currentTeam.squad.predicted_points ?? 0).toFixed(1)}
                              </div>
                            </div>
                            <div className="text-right">
                              <div className="text-xs text-gray-400">Total Cost</div>
                              <div className="text-lg font-mono">
                                £{currentTeam.squad.total_cost}m
                              </div>
                            </div>
                            <div className="text-right">
                              <div className="text-xs text-gray-400">Formation</div>
                              <div className="text-lg font-mono">
                                {currentTeam.squad.formation}
                              </div>
                            </div>
                          </div>
                        </div>

                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                          {/* Starting XI */}
                          <div>
                            <h4 className="text-sm text-gray-400 mb-3 uppercase font-semibold">Starting XI</h4>
                            <div className="space-y-2">
                              {currentTeam.squad.starting_xi.map((player) => (
                                <div key={player.id} className="flex items-center justify-between text-sm py-2 px-3 bg-[#0b0b14] rounded border border-[#2a2a4a]/50">
                                  <div className="flex items-center gap-2">
                                    <span className="font-medium">{player.name}</span>
                                    <span className={`px-1.5 py-0.5 rounded text-xs ${getPositionClass(player.position)}`}>
                                      {player.position}
                                    </span>
                                  </div>
                                  <div className="text-right">
                                    <div className="text-xs text-gray-400">{player.team}</div>
                                    <div className="font-mono text-xs">£{player.price}m</div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>

                          {/* Bench */}
                          <div>
                            <h4 className="text-sm text-gray-400 mb-3 uppercase font-semibold">Bench</h4>
                            <div className="space-y-2">
                              {currentTeam.squad.bench.map((player) => (
                                <div key={player.id} className="flex items-center justify-between text-sm py-2 px-3 bg-[#0b0b14] rounded border border-[#2a2a4a]/50 opacity-75">
                                  <div className="flex items-center gap-2">
                                    <span>{player.name}</span>
                                    <span className={`px-1.5 py-0.5 rounded text-xs ${getPositionClass(player.position)}`}>
                                      {player.position}
                                    </span>
                                  </div>
                                  <div className="text-right">
                                    <div className="text-xs text-gray-500">{player.team}</div>
                                    <div className="font-mono text-xs text-gray-500">£{player.price}m</div>
                                  </div>
                                </div>
                              ))}
                            </div>
                            <div className="mt-4 pt-4 border-t border-[#2a2a4a]">
                              <div className="flex items-center gap-2 text-sm mb-2">
                                <span className="text-gray-400">Captain:</span>
                                <span className="font-semibold text-[#00ff87]">{currentTeam.squad.captain.name}</span>
                                <span className="text-[#00ff87] font-mono">({(currentTeam.squad.captain.predicted ?? 0).toFixed(1)} × 2)</span>
                              </div>
                              <div className="flex items-center gap-2 text-sm">
                                <span className="text-gray-400">Vice-Captain:</span>
                                <span>{currentTeam.squad.vice_captain.name}</span>
                                <span className="text-gray-500 font-mono">({(currentTeam.squad.vice_captain.predicted ?? 0).toFixed(1)})</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        })()}

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

        {/* Differentials Tab */}
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
      </main>

      {/* Footer */}
      <footer className="border-t border-[#2a2a4a] py-6 mt-12">
        <div className="max-w-6xl mx-auto px-6 text-center text-gray-500 text-sm">
          FPL Squad Suggester • AI-powered predictions • Not affiliated with Premier League
        </div>
      </footer>
    </div>
  )
}

export default App
