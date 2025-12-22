import { useState, useEffect, useCallback, useMemo } from 'react'
import { 
  Users, TrendingUp, RefreshCw, Zap, Award, 
  ChevronRight, ChevronDown, ChevronUp, Star, Target, Flame, AlertTriangle, Plane,
  ArrowRightLeft, Search, Plus, X, Trash2, Trophy, Home, Brain
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
  // IMPORTANT: For "Quick Transfers" this should be the user's SELLING price.
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
  const [squadLSTM, setSquadLSTM] = useState<SuggestedSquad | null>(null)
  const [statisticsMethod, setStatisticsMethod] = useState<'combined' | 'heuristic' | 'form' | 'fixture'>('combined')
  const [topPicks, setTopPicks] = useState<Record<string, Player[]>>({})
  const [differentials, setDifferentials] = useState<Player[]>([])
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [activeTab, setActiveTab] = useState('home')
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [countdown, setCountdown] = useState<{ days: number; hours: number; minutes: number; seconds: number } | null>(null)
  const [savingSquad, setSavingSquad] = useState(false)
  const [loadingSquad, setLoadingSquad] = useState(false)
  const [updatingSquad, setUpdatingSquad] = useState(false)
  const [deletingSquad, setDeletingSquad] = useState(false)
  
  // Transfer tab state
  const [mySquad, setMySquad] = useState<SquadPlayer[]>([])
  const [bank, setBank] = useState(0)
  const [bankInput, setBankInput] = useState('0')
  const [freeTransfers, setFreeTransfers] = useState(1)
  const [transferSuggestions, setTransferSuggestions] = useState<TransferSuggestion[]>([])
  const [squadAnalysis, setSquadAnalysis] = useState<any[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Player[]>([])
  const [searchPosition, setSearchPosition] = useState<string>('')
  const [transferLoading, setTransferLoading] = useState(false)

  // Wildcard state
  const [wildcardLoading, setWildcardLoading] = useState(false)
  const [wildcardPlan, setWildcardPlan] = useState<any>(null)
  
  // Expanded groups for transfer suggestions
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  
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

  // Load saved squads from API on mount
  const loadSavedSquads = async () => {
    setLoadingSavedSquads(true)
    try {
      const response = await fetch(`${API_BASE}/api/saved-squads`)
      if (!response.ok) {
        console.error(`Failed to load saved squads: HTTP ${response.status}`, await response.text().catch(() => ''))
        setSavedSquads([])
        return
      }
      const res = await response.json()
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
            bankInput: String(squadData.bank || 0),
            freeTransfers: squadData.freeTransfers || 1,
          }
        })
        setSavedSquads(mapped)
        console.log(`Loaded ${mapped.length} saved squad(s)`)
      } else {
        console.warn('Unexpected response format from saved-squads endpoint:', res)
        setSavedSquads([])
      }
    } catch (err) {
      console.error('Failed to load saved squads:', err)
      setSavedSquads([])
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
          if (typeof d.bank === 'number') {
            setBank(d.bank)
            setBankInput(String(d.bank))
          }
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
      setBankInput(String(squadData.bank ?? 0))
      setFreeTransfers(squadData.freeTransfers ?? 1)
      setSaveName(data.name || 'My Squad')
      
      // Reset view when loading a new squad
      setWildcardPlan(null)
      setTransferSuggestions([])
      setSquadAnalysis([])
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

  useEffect(() => {
    // Lazy-load heavy tabs only when the user opens them
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
      if (activeTab === 'picks') {
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
    // Use standard FPL formation (3-5-2) to show all slots
    const formation = { def: 5, mid: 5, fwd: 3, gk: 1 }

    // Group current squad by position
    const byPosition = {
      GK: mySquad.filter(p => p.position === 'GK'),
      DEF: mySquad.filter(p => p.position === 'DEF'),
      MID: mySquad.filter(p => p.position === 'MID'),
      FWD: mySquad.filter(p => p.position === 'FWD'),
    }

    // Create arrays with empty slots
    const renderRow = (players: any[], expectedCount: number, position: string) => {
      const slots: any[] = []
      for (let i = 0; i < expectedCount; i++) {
        if (i < players.length) {
          slots.push(players[i])
        } else {
          slots.push(null) // null indicates empty slot
        }
      }
      return slots
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
            {renderRow(byPosition.GK, 1, 'GK').map((slot, idx) => {
              const isEmpty = slot === null
              const isFull = isPositionFull('GK')
              return (
                <div 
                  key={`gk-${idx}`} 
                  onClick={() => !isEmpty && !isFull && handleSlotClick('GK')} 
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
            {renderRow(byPosition.DEF, formation.def, 'DEF').map((slot, idx) => {
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
            {renderRow(byPosition.MID, formation.mid, 'MID').map((slot, idx) => {
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
            {renderRow(byPosition.FWD, formation.fwd, 'FWD').map((slot, idx) => {
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
    { id: 'selected_teams', icon: Trophy, label: 'Free Hit of the Week', shortLabel: 'Free Hit', color: 'text-yellow-400', description: 'View your saved free hit team selections' },
    { id: 'picks', icon: Star, label: 'Top Picks', shortLabel: 'Picks', color: 'text-yellow-400', description: 'Top player picks by position' },
    { id: 'differentials', icon: Target, label: 'Differentials', shortLabel: 'Diffs', color: 'text-pink-400', description: 'Low ownership, high potential players' },
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
        

        {/* Transfers Tab (Quick Transfers 1-3, Wildcard 4+) */}
        {activeTab === 'transfers' && (
          <div className={`space-y-6 transition-colors duration-300 ${freeTransfers >= 4 ? 'bg-gradient-to-br from-purple-900/5 to-indigo-900/5 rounded-lg p-1' : ''}`}>
            {/* Instructions */}
            <div className={`card transition-colors duration-300 ${freeTransfers >= 4 ? 'bg-[#0f0f1a]/80 border-purple-500/20' : ''}`}>
              <div className={`card-header transition-colors duration-300 ${freeTransfers >= 4 ? 'border-purple-500/30' : ''}`}>
                <ArrowRightLeft className={`w-5 h-5 transition-colors duration-300 ${freeTransfers >= 4 ? 'text-purple-400' : 'text-[#00ff87]'}`} />
                Transfers
                {freeTransfers >= 4 && (
                  <span className="ml-2 text-xs text-purple-400 font-medium">Wildcard Mode</span>
                )}
              </div>
              <p className="text-gray-400 text-sm mb-4">
                {freeTransfers <= 3 
                  ? 'Add your current squad below and get AI-powered transfer suggestions (1-3 transfers) considering both short-term (next GW) and long-term (next 5 GWs) fixtures.'
                  : 'Get a coordinated multi-transfer plan (4+ transfers) optimized for total points gain. All transfers work together as a cohesive unit, enforcing formation constraints.'
                }
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
              <div className="space-y-6">
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
                    {['', 'GK', 'DEF', 'MID', 'FWD'].map(pos => {
                      const isFull = pos ? isPositionFull(pos) : false
                      const count = pos ? getPositionCount(pos) : 0
                      const limit = pos ? POSITION_LIMITS[pos] : 0
                      
                      return (
                      <button
                        key={pos}
                        onClick={() => {
                          setSearchPosition(pos)
                          // If the user hasn't typed anything, show cheapest options for that position.
                          // If they have typed 2+ chars, search by name.
                          searchPlayers(searchQuery, pos)
                        }}
                          className={`px-3 py-1 rounded text-sm relative ${
                          searchPosition === pos 
                            ? 'bg-[#00ff87] text-[#0f0f1a]' 
                              : isFull
                              ? 'bg-[#2a2a4a] text-gray-500 opacity-60'
                            : 'bg-[#2a2a4a] text-gray-300 hover:bg-[#3a3a5a]'
                        }`}
                          title={isFull ? `${pos} position full (${count}/${limit})` : ''}
                      >
                        {pos || 'All'}
                          {isFull && (
                            <span className="ml-1 text-[10px]">({count}/{limit})</span>
                          )}
                      </button>
                      )
                    })}
                  </div>
                  
                  {/* Search Results */}
                  {searchResults.length > 0 && (
                    <div className="bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg max-h-60 overflow-y-auto">
                      {searchResults.map(player => {
                        const alreadyInSquad = mySquad.find(p => p.id === player.id) !== undefined
                        const positionFull = isPositionFull(player.position)
                        const disabled = alreadyInSquad || positionFull
                        const positionCount = getPositionCount(player.position)
                        const positionLimit = POSITION_LIMITS[player.position] || 0
                        
                        return (
                        <button
                          key={player.id}
                          onClick={() => addToSquad(player)}
                            disabled={disabled}
                            className={`w-full flex items-center justify-between p-3 border-b border-[#2a2a4a] last:border-0 transition-all ${
                              disabled 
                                ? 'opacity-50 cursor-not-allowed bg-[#0b0b14]' 
                                : 'hover:bg-[#1f1f3a] cursor-pointer'
                            }`}
                            title={positionFull ? `${player.position} position full (${positionCount}/${positionLimit})` : alreadyInSquad ? 'Already in squad' : ''}
                          >
                            <div className="flex items-center gap-3 flex-1 min-w-0">
                              <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getPositionClass(player.position)} flex-shrink-0`}>
                              {player.position}
                            </span>
                              <div className="text-left min-w-0 flex-1">
                                <div className="font-medium truncate">{player.name}</div>
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
                                  {positionFull && (
                                    <span className="ml-2 text-orange-400 text-[10px]">
                                      • {player.position} full ({positionCount}/{positionLimit})
                                    </span>
                                  )}
                              </div>
                            </div>
                          </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                            <span className="text-sm font-mono">£{player.price}m</span>
                              {disabled ? (
                                <X className="w-4 h-4 text-gray-500" />
                              ) : (
                            <Plus className="w-4 h-4 text-[#00ff87]" />
                              )}
                          </div>
                        </button>
                        )
                      })}
                    </div>
                  )}
                </div>
                
                {/* Bank & Free Transfers - Always visible in stable position */}
                <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 mt-6 pt-4 border-t border-[#2a2a4a]">
                  <div className="flex items-center gap-2">
                    <label className="text-sm text-gray-400 whitespace-nowrap">Bank (£m)</label>
                    <input
                      type="number"
                      step="0.1"
                      value={bankInput}
                      onChange={(e) => {
                        const val = e.target.value
                        // Allow empty string, single dot, or valid numbers
                        if (val === '' || val === '.' || /^-?\d*\.?\d*$/.test(val)) {
                          setBankInput(val)
                          const numVal = parseFloat(val)
                          if (!isNaN(numVal) && isFinite(numVal)) {
                            setBank(numVal)
                          } else if (val === '' || val === '.' || val === '-') {
                            setBank(0)
                          }
                        }
                      }}
                      onBlur={(e) => {
                        const val = e.target.value
                        const numVal = parseFloat(val)
                        if (isNaN(numVal) || !isFinite(numVal) || numVal < 0) {
                          setBankInput('0')
                          setBank(0)
                        } else {
                          // Preserve decimals if they exist
                          const formatted = numVal.toString()
                          setBankInput(formatted)
                          setBank(numVal)
                        }
                      }}
                      className="w-24 px-3 py-1.5 sm:py-1 bg-[#0f0f1a] border border-[#2a2a4a] rounded focus:border-[#00ff87] focus:outline-none text-sm"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="text-sm text-gray-400 whitespace-nowrap">Free Transfers</label>
                    <select
                      value={freeTransfers}
                      onChange={(e) => {
                        const val = parseInt(e.target.value) || 1
                        setFreeTransfers(val)
                        // Clear previous results when switching
                        setTransferSuggestions([])
                        setWildcardPlan(null)
                      }}
                      className="w-24 px-3 py-1.5 sm:py-1 bg-[#0f0f1a] border border-[#2a2a4a] rounded focus:border-[#00ff87] focus:outline-none text-sm"
                    >
                      {Array.from({ length: 15 }, (_, i) => i + 1).map(num => (
                        <option key={num} value={num}>{num}</option>
                      ))}
                    </select>
                  </div>
                </div>
                
                {/* Current Squad - Pitch Formation (only show when no results) */}
                {!wildcardPlan && !groupedTransferSuggestions && (
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="font-medium">Your Squad ({mySquad.length}/15)</h3>
                      <div className="flex items-center gap-3">
                        {/* Generate Wildcard/Suggestions Button */}
                        <button
                          onClick={async () => {
                            if (mySquad.length < 15) {
                              setError('Please add all 15 players to your squad')
                              return
                            }
                            setError(null)
                            
                            if (freeTransfers <= 3) {
                              // Quick Transfers
                              await getTransferSuggestions()
                            } else {
                              // Wildcard
                              setWildcardLoading(true)
                              setTransferSuggestions([])
                              try {
                                const res = await fetch(`${API_BASE}/api/wildcard`, {
                                  method: 'POST',
                                  headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify({
                                    squad: mySquad,
                                    bank: bank,
                                    free_transfers: freeTransfers,
                                  }),
                                })
                                if (!res.ok) {
                                  const errorData = await res.json().catch(() => ({ detail: 'Unknown error' }))
                                  throw new Error(errorData.detail || `HTTP ${res.status}`)
                                }
                                const data = await res.json()
                                setWildcardPlan(data)
                              } catch (err) {
                                console.error('Wildcard error:', err)
                                setError(err instanceof Error ? err.message : 'Failed to generate wildcard plan')
                                setWildcardPlan(null)
                              } finally {
                                setWildcardLoading(false)
                              }
                            }
                          }}
                          disabled={mySquad.length < 15 || transferLoading || wildcardLoading}
                          title={mySquad.length < 15 ? `Squad incomplete (${mySquad.length}/15 players). Add all 15 players to generate suggestions.` : ''}
                          className={`btn text-xs sm:text-sm transition-colors duration-300 ${
                            mySquad.length < 15
                              ? 'bg-gray-600 text-gray-400 cursor-not-allowed border-gray-500'
                              : freeTransfers >= 4 
                              ? 'bg-purple-600 hover:bg-purple-700 text-white border-purple-500' 
                              : 'btn-primary'
                          }`}
                        >
                          {(transferLoading || wildcardLoading) ? (
                            <>
                              <RefreshCw className="w-3 h-3 sm:w-4 sm:h-4 animate-spin inline mr-1 sm:mr-2" />
                              <span className="hidden sm:inline">Loading...</span>
                            </>
                          ) : (
                            freeTransfers <= 3 ? 'Get Suggestions' : 'Generate Wildcard'
                          )}
                        </button>
                        {mySquad.length > 0 && (
                          <button 
                            onClick={() => setMySquad([])}
                            className="text-xs text-red-400 hover:text-red-300"
                          >
                            Clear All
                          </button>
                        )}
                      </div>
                    </div>
                    
                    <div className="mb-4">
                      <div className="text-xs text-gray-500 mb-2">
                        Prices from search are <span className="text-gray-300">current FPL prices</span>. Your in-game
                        <span className="text-gray-300"> selling price</span> can be different (e.g. you bought before a price rise).
                        Click a player to edit price or remove.
                      </div>
                      {renderTransfersPitch()}
                    </div>

                    {/* Player list for editing prices */}
                    {mySquad.length > 0 && (
                      <div className="space-y-2 max-h-60 overflow-y-auto border-t border-[#2a2a4a] pt-4">
                        {mySquad.map(player => (
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
                    )}
                  </div>
                )}
              </div>
            </div>
            
            {/* Transfer Suggestions */}
            {groupedTransferSuggestions && (
              <div className="card">
                <div className="card-header">
                  <TrendingUp className="w-5 h-5 text-[#00ff87]" />
                  Transfer Suggestions
                </div>
                
                <div className="space-y-4">
                    {/* Hold Suggestions */}
                    {groupedTransferSuggestions.holdSuggestions.map((suggestion, i) => (
                      <div key={`hold-${i}`} className="p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
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
                    ))}
                    
                    {/* Grouped Transfer Suggestions */}
                    {groupedTransferSuggestions.sortedGroups.map((group, groupIndex) => {
                      const groupKey = `group-${group.outPlayer.id || group.outPlayer.name}`
                      const isExpanded = expandedGroups.has(groupKey)
                      const firstOption = group.suggestions[0]
                      const otherOptions = group.suggestions.slice(1)
                      
                      const renderOption = (suggestion: TransferSuggestion, optionIndex: number) => (
                        <div key={`option-${optionIndex}`} className="p-3 bg-green-500/5 rounded-lg border border-green-500/20">
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-semibold text-green-400">#{optionIndex + 1}</span>
                              <span className="text-sm font-medium text-gray-200">Transfer In</span>
                            </div>
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          suggestion.points_gain > 0 ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                        }`}>
                          {suggestion.points_gain > 0 ? '+' : ''}{suggestion.points_gain} pts
                        </span>
                      </div>
                      
                          <div className="flex items-center gap-2 flex-wrap mb-2">
                            <span className="font-medium text-sm">{suggestion.in.name}</span>
                            {suggestion.in.european_comp && (
                              <span className="px-1 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-400">
                                {suggestion.in.european_comp}
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-gray-400">{suggestion.in.team} • £{suggestion.in.price}m</div>
                          <div className="text-xs text-gray-500 mt-1">
                            vs {suggestion.in.fixture} (FDR {suggestion.in.fixture_difficulty}) • Form: {suggestion.in.form}
                          </div>
                          
                          {/* Form Upgrade */}
                          {suggestion.out.form && suggestion.in.form && parseFloat(suggestion.in.form) > parseFloat(suggestion.out.form) && (
                            <div className="mt-2 flex items-center gap-1 text-xs">
                              <span className="text-yellow-400">💡</span>
                              <span className="text-[#00ff87] font-medium">
                                Form upgrade: {suggestion.out.form} → {suggestion.in.form}
                              </span>
                            </div>
                          )}
                          
                          {/* Additional reasons */}
                          {suggestion.all_reasons.length > 0 && (
                            <div className="mt-2 text-xs text-gray-400">
                              {suggestion.all_reasons[0] && (
                                <div className="mb-1">
                                  {suggestion.all_reasons[0].includes('Also:') ? suggestion.all_reasons[0] : `Also: ${suggestion.all_reasons[0]}`}
                                </div>
                              )}
                              {suggestion.all_reasons.slice(1).map((reason: string, idx: number) => (
                                <div key={idx} className="text-gray-500">• {reason}</div>
                              ))}
                            </div>
                          )}
                          
                          {/* Why square - prettied up */}
                          {suggestion.teammate_comparison?.why && (
                            <div className="mt-3 pt-3 border-t border-[#2a2a4a]">
                              <div className="p-3 bg-gradient-to-br from-[#1a1a2e]/60 to-[#0f0f1a] rounded-lg border border-[#00ff87]/20">
                                <div className="flex items-start gap-2 mb-2">
                                  <span className="text-[#00ff87] text-sm">💡</span>
                                  <div className="flex-1">
                                    <div className="text-xs font-semibold text-gray-300 mb-1">
                                      Why {suggestion.in.name} over other {suggestion.teammate_comparison.team} {suggestion.teammate_comparison.position} options?
                                    </div>
                                    <div className="text-xs text-gray-400 leading-relaxed">
                                      {suggestion.teammate_comparison.why}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                          
                          {/* Cost and FDR */}
                          <div className="flex gap-4 mt-2 text-xs text-gray-400 pt-2 border-t border-[#2a2a4a]/50">
                            <span>Cost: <span className={suggestion.cost > 0 ? 'text-red-400' : 'text-green-400'}>{suggestion.cost > 0 ? '+' : ''}£{suggestion.cost}m</span></span>
                            <span>5GW Avg FDR: <span className="text-gray-300">{suggestion.out.avg_fixture_5gw}</span> → <span className="text-[#00ff87]">{suggestion.in.avg_fixture_5gw}</span></span>
                          </div>
                        </div>
                      )
                      
                      return (
                        <div key={groupKey} className="p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                          <div className="flex items-center justify-between mb-3">
                            <span className="text-lg font-bold text-[#00ff87]">#{groupIndex + 1}</span>
                            {otherOptions.length > 0 && (
                              <button
                                onClick={() => {
                                  const newExpanded = new Set(expandedGroups)
                                  if (isExpanded) {
                                    newExpanded.delete(groupKey)
                                  } else {
                                    newExpanded.add(groupKey)
                                  }
                                  setExpandedGroups(newExpanded)
                                }}
                                className="flex items-center gap-1 text-xs text-gray-400 hover:text-[#00ff87] transition-colors"
                              >
                                {isExpanded ? (
                                  <>
                                    <ChevronUp className="w-3 h-3" />
                                    <span>Hide {otherOptions.length} more</span>
                                  </>
                                ) : (
                                  <>
                                    <ChevronDown className="w-3 h-3" />
                                    <span>Show {otherOptions.length} more option{otherOptions.length > 1 ? 's' : ''}</span>
                                  </>
                                )}
                              </button>
                            )}
                          </div>
                          
                          {/* Transfer Out Player (shown once per group) */}
                          <div className="mb-4 p-3 bg-red-500/10 rounded-lg border border-red-500/30">
                            <div className="text-xs text-red-400 mb-2 font-semibold">Transfer Out</div>
                          <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-medium text-base">{group.outPlayer.name}</span>
                              {group.outPlayer.european_comp && (
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                  group.outPlayer.rotation_risk === 'high' ? 'bg-orange-500/30 text-orange-400' :
                                  group.outPlayer.rotation_risk === 'medium' ? 'bg-yellow-500/30 text-yellow-400' :
                                'bg-blue-500/20 text-blue-400'
                              }`}>
                                  {group.outPlayer.european_comp}
                                </span>
                              )}
                              {group.outPlayer.status && (
                                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/20 text-red-400">
                                  {group.outPlayer.status === 'i' ? 'Injured' : 
                                   group.outPlayer.status === 'd' ? 'Doubtful' :
                                   group.outPlayer.status === 's' ? 'Suspended' : 'Unavailable'}
                              </span>
                            )}
                          </div>
                            <div className="text-sm text-gray-400 mt-1">{group.outPlayer.team} • £{group.outPlayer.price}m</div>
                            <div className="text-xs text-gray-500 mt-1">
                              vs {group.outPlayer.fixture} (FDR {group.outPlayer.fixture_difficulty}) • Form: {group.outPlayer.form}
                          </div>
                        </div>
                        
                          {/* Transfer In Options (ranked) */}
                          <div className="space-y-3">
                            {/* First option - always visible */}
                            {firstOption && renderOption(firstOption, 0)}
                            
                            {/* Other options - hidden behind dropdown */}
                            {isExpanded && otherOptions.map((suggestion, optionIndex) => 
                              renderOption(suggestion, optionIndex + 1)
                            )}
                          </div>
                          </div>
                      )
                    })}
                        </div>
                      </div>
            )}
            
            {/* Wildcard Results - shown when freeTransfers >= 4 */}
            {freeTransfers >= 4 && wildcardPlan && (
              <div className="space-y-6">
                {/* Summary Card */}
                <div className="card">
                  <div className="card-header">
                    <TrendingUp className="w-5 h-5 text-[#00ff87]" />
                    Rebuild Summary
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    <div>
                      <div className="text-red-400 text-sm mb-1">Before Total</div>
                      <div className="text-xl font-bold text-red-400">
                        {wildcardPlan.before_total_points?.toFixed(1) || '0.0'}
                      </div>
                    </div>
                    <div>
                      <div className="text-gray-400 text-sm mb-1">After Total</div>
                      <div className="text-xl font-bold text-[#00ff87]">
                        {wildcardPlan.after_total_points?.toFixed(1) || '0.0'}
                      </div>
                    </div>
                    <div>
                      <div className="text-gray-400 text-sm mb-1">Points Gain</div>
                      <div className="text-xl font-bold text-[#00ff87]">
                        +{wildcardPlan.total_points_gain?.toFixed(1) || '0.0'}
                      </div>
                    </div>
                    <div>
                      <div className="text-gray-400 text-sm mb-1">Net Cost</div>
                      <div className={`text-xl font-bold ${wildcardPlan.total_cost < 0 ? 'text-green-400' : wildcardPlan.total_cost > 0 ? 'text-red-400' : 'text-gray-300'}`}>
                        {wildcardPlan.total_cost > 0 ? '+' : ''}£{wildcardPlan.total_cost?.toFixed(1) || '0.0'}m
                      </div>
                    </div>
                  </div>
                      <div className="mt-3 pt-3 border-t border-[#2a2a4a]">
                    <div className="text-xs text-gray-400">
                      Transfers: {wildcardPlan.transfers_out?.length || 0} • 
                      Kept: {wildcardPlan.kept_players?.length || (mySquad.length - (wildcardPlan.transfers_out?.length || 0))}
                    </div>
                  </div>
                  {wildcardPlan.combined_rationale && (
                    <div className="mt-4 p-3 bg-gradient-to-br from-[#1a1a2e]/60 to-[#0f0f1a] rounded-lg border border-[#00ff87]/20">
                      <div className="text-sm font-semibold text-gray-300 mb-2">Why This Combination Works</div>
                      <div className="text-xs text-gray-400 leading-relaxed">
                        {wildcardPlan.combined_rationale}
                      </div>
                          </div>
                        )}
                            </div>
                
                {/* Before/After Squad Comparison - Pitch Formation (on top) */}
                {wildcardPlan.resulting_squad?.squad && (
                  <div className="card">
                    <div className="card-header">
                      <Users className="w-5 h-5 text-[#00ff87]" />
                      Squad Comparison
                    </div>
                    {(() => {
                      // Construct before starting XI from mySquad
                      // Group by position and select best players for starting XI
                      const byPosition = {
                        GK: mySquad.filter((p: SquadPlayer) => p.position === 'GK'),
                        DEF: mySquad.filter((p: SquadPlayer) => p.position === 'DEF'),
                        MID: mySquad.filter((p: SquadPlayer) => p.position === 'MID'),
                        FWD: mySquad.filter((p: SquadPlayer) => p.position === 'FWD'),
                      }
                      
                      // Determine formation from before squad or use default
                      // Try to infer from squad structure, default to 3-5-2
                      const beforeFormation = wildcardPlan.before_formation || 
                        (() => {
                          // Infer formation from squad structure
                          const defCount = Math.min(byPosition.DEF.length, 5)
                          const midCount = Math.min(byPosition.MID.length, 5)
                          const fwdCount = Math.min(byPosition.FWD.length, 3)
                          // Ensure we have 11 players
                          if (defCount + midCount + fwdCount + 1 === 11) {
                            return `${defCount}-${midCount}-${fwdCount}`
                          }
                          return '3-5-2' // Default
                        })()
                      
                      const formationLayout = parseFormation(beforeFormation)
                      
                      // Use full squad (all 15 players) for before
                      const beforeFullSquad = mySquad
                      
                      // Use full squad (all 15 players) for after
                      const afterFullSquad = wildcardPlan.resulting_squad.squad
                      
                      // Handle formation - could be string "3-5-2" or dict {"GK": 1, "DEF": 3, "MID": 5, "FWD": 2}
                      const afterFormationRaw = wildcardPlan.resulting_squad.formation || '3-5-2'
                      const afterFormation = typeof afterFormationRaw === 'string' 
                        ? afterFormationRaw 
                        : `${afterFormationRaw.DEF || 3}-${afterFormationRaw.MID || 5}-${afterFormationRaw.FWD || 2}`
                      
                      return renderBeforeAfterPitch(
                        beforeFullSquad,
                        afterFullSquad,
                        beforeFormation,
                        afterFormation,
                        wildcardPlan.transfers_out || [],
                        wildcardPlan.transfers_in || []
                      )
                    })()}
                  </div>
                )}
                
                {/* Kept Players and Transfer Breakdown - Side by Side (below pitch) */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Kept Players */}
                  {wildcardPlan.kept_players && wildcardPlan.kept_players.length > 0 && (
                    <div className="card">
                      <div className="card-header">
                        <Star className="w-5 h-5 text-yellow-400" />
                        Kept Players ({wildcardPlan.kept_players.length})
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        {wildcardPlan.kept_players.map((player: any) => (
                          <div key={player.id} className="p-2 bg-[#0f0f1a] rounded border border-[#2a2a4a]">
                            <div className="flex flex-col">
                              <div className="flex items-center justify-between mb-0.5">
                                <span className="font-medium text-xs truncate">{player.name}</span>
                                <span className="text-[10px] text-gray-400 flex-shrink-0 ml-1">£{player.price}m</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-[10px] text-gray-500">{player.team}</span>
                                <span className="text-[10px] text-[#00ff87] font-mono">{player.predicted?.toFixed(1) || '0.0'}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  
                  {/* Individual Transfer Breakdown */}
                  {wildcardPlan.individual_breakdowns && wildcardPlan.individual_breakdowns.length > 0 && (
                    <div className="card">
                      <div className="card-header">
                        <ArrowRightLeft className="w-5 h-5 text-[#00ff87]" />
                        Transfer Breakdown ({wildcardPlan.individual_breakdowns.length})
                      </div>
                      <div className="space-y-2 max-h-[400px] overflow-y-auto">
                        {wildcardPlan.individual_breakdowns.map((transfer: any, i: number) => (
                          <div key={i} className="p-2.5 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                            <div className="flex items-center gap-1.5 mb-1.5">
                              <span className="text-xs font-semibold text-[#00ff87]">#{i + 1}</span>
                              <span className="text-[10px] text-red-400">OUT:</span>
                              <span className="text-xs font-medium truncate">{transfer.out?.name}</span>
                              <ArrowRightLeft className="w-3 h-3 text-gray-500 flex-shrink-0" />
                              <span className="text-[10px] text-green-400">IN:</span>
                              <span className="text-xs font-medium truncate">{transfer.in?.name}</span>
                            </div>
                            {transfer.reason && (
                              <div className="text-[10px] text-gray-400 leading-tight">{transfer.reason}</div>
                            )}
                            <div className="flex items-center gap-2 mt-1.5 text-[10px]">
                              <span className={`${transfer.points_gain > 0 ? 'text-green-400' : 'text-gray-400'}`}>
                                {transfer.points_gain > 0 ? '+' : ''}{transfer.points_gain} pts
                              </span>
                              <span className="text-gray-500">•</span>
                              <span className={transfer.cost > 0 ? 'text-red-400' : transfer.cost < 0 ? 'text-green-400' : 'text-gray-400'}>
                                {transfer.cost > 0 ? '+' : ''}£{transfer.cost}m
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
            
        {/* Squad Analysis - Only shown in Quick Transfers (1-3 transfers) */}
        {activeTab === 'transfers' && freeTransfers <= 3 && squadAnalysis.length > 0 && (
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

        {/* Free Hit of the Week Tab */}
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
                  Free Hit of the Week
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

                        <div className="space-y-6">
                          {/* Starting XI - Pitch Formation */}
                          <div>
                            <h4 className="text-sm text-gray-400 mb-3 uppercase font-semibold">Starting XI • {currentTeam.squad.formation}</h4>
                            {renderPitchFormation(currentTeam.squad.starting_xi, currentTeam.squad.formation)}
                          </div>

                          {/* Bench */}
                          <div>
                            <h4 className="text-sm text-gray-400 mb-3 uppercase font-semibold">Bench</h4>
                            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                              {[...currentTeam.squad.bench].sort((a: any, b: any) => {
                                // Order: GK, DEF, MID, FWD
                                const order: Record<string, number> = { 'GK': 0, 'DEF': 1, 'MID': 2, 'FWD': 3 }
                                return (order[a.position] ?? 99) - (order[b.position] ?? 99)
                              }).map((player) => (
                                <div key={player.id} className="p-3 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a] opacity-75">
                                  <div className="flex items-center gap-1 mb-2">
                                    <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getPositionClass(player.position)}`}>
                                      {player.position}
                                    </span>
                                  </div>
                                  <div className="font-medium text-sm">{player.name}</div>
                                  <div className="text-xs text-gray-400">{player.team} • £{player.price}m</div>
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
                                <span className="font-semibold text-purple-400">{currentTeam.squad.vice_captain.name}</span>
                                <span className="text-purple-400 font-mono">({(currentTeam.squad.vice_captain.predicted ?? 0).toFixed(1)})</span>
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
    </div>
  )
}

export default App
