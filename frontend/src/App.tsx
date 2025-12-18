import { useState, useEffect } from 'react'
import { 
  Users, TrendingUp, RefreshCw, Zap, Award, 
  ChevronRight, Star, Target, Flame, AlertTriangle, Plane,
  ArrowRightLeft, Search, Plus, X, Trash2
} from 'lucide-react'

const API_BASE = 'http://localhost:8001'

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

  useEffect(() => {
    loadInitial()
  }, [])

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
        await ensureSquadLoaded('combined')
      } else if (activeTab === 'squad_heuristic') {
        setSquadHeuristic(null)
        await ensureSquadLoaded('heuristic')
      } else if (activeTab === 'squad_form') {
        setSquadForm(null)
        await ensureSquadLoaded('form')
      } else if (activeTab === 'squad_fixture') {
        setSquadFixture(null)
        await ensureSquadLoaded('fixture')
      } else if (activeTab === 'picks') {
        setTopPicks({})
        await ensurePicksLoaded()
      } else if (activeTab === 'differentials') {
        setDifferentials([])
        await ensureDifferentialsLoaded()
      }
    } catch (err) {
      console.error('Refresh error:', err)
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
      const res = await fetch(`${API_BASE}/api/transfer-suggestions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          squad: mySquad,
          bank: bank,
          free_transfers: freeTransfers,
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
      <header className="bg-[#1a1a2e] border-b border-[#2a2a4a] px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-[#00ff87] to-[#00cc6a] rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-[#0f0f1a]" />
            </div>
            <div>
              <h1 className="font-bold text-lg">FPL Squad Suggester</h1>
              <p className="text-xs text-gray-400">
                {gameweek?.next ? `GW${gameweek.next.id} • Deadline: ${formatDeadline(gameweek.next.deadline)}` : 'Loading...'}
              </p>
            </div>
          </div>
          
          <button onClick={refresh} className="btn btn-secondary flex items-center gap-2">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </header>

      {/* Navigation */}
      <nav className="bg-[#1a1a2e]/50 border-b border-[#2a2a4a] px-6">
        <div className="max-w-6xl mx-auto flex gap-1">
          {[
            { id: 'transfers', icon: ArrowRightLeft, label: 'My Transfers' },
            { id: 'squad_combined', icon: Users, label: 'Squad • Combined' },
            { id: 'squad_heuristic', icon: Zap, label: 'Squad • Heuristic' },
            { id: 'squad_form', icon: TrendingUp, label: 'Squad • Form' },
            { id: 'squad_fixture', icon: Target, label: 'Squad • Fixture' },
            { id: 'picks', icon: Star, label: 'Top Picks' },
            { id: 'differentials', icon: Target, label: 'Differentials' },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 border-b-2 transition-colors ${
                activeTab === tab.id 
                  ? 'border-[#00ff87] text-white' 
                  : 'border-transparent text-gray-400 hover:text-white'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-6xl mx-auto p-6">
        
        {/* Squad Tabs */}
        {isSquadTab && !currentSquad && (
          <div className="text-center text-gray-400 py-8">Loading squad...</div>
        )}

        {isSquadTab && currentSquad && (
          <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
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
                <div className="text-2xl font-bold text-[#00ff87]">{currentSquad.predicted_points}</div>
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
              <div className="flex gap-4">
                <div className="flex-1 p-4 bg-yellow-500/10 rounded-lg border border-yellow-500/30">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-yellow-400 text-lg font-bold">©</span>
                    <span className="font-semibold text-lg">{currentSquad.captain.name}</span>
                  </div>
                  <span className="text-gray-400">Predicted: <span className="text-[#00ff87] font-mono">{currentSquad.captain.predicted} × 2 = {(currentSquad.captain.predicted * 2).toFixed(1)}</span></span>
                </div>
                <div className="flex-1 p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-gray-400">V</span>
                    <span className="font-medium">{currentSquad.vice_captain.name}</span>
                  </div>
                  <span className="text-gray-400">Predicted: <span className="font-mono">{currentSquad.vice_captain.predicted}</span></span>
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
                        <td className="py-3 text-right font-mono text-[#00ff87] font-semibold">{player.predicted}</td>
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
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
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
                    <div className="text-sm text-[#00ff87] font-mono mt-1">{player.predicted} pts</div>
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
              
              {/* Squad Input */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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
              <div className="flex gap-4 mt-6 pt-4 border-t border-[#2a2a4a]">
                <div>
                  <label className="text-sm text-gray-400">Bank (£m)</label>
                  <input
                    type="number"
                    step="0.1"
                    value={bank}
                    onChange={(e) => setBank(parseFloat(e.target.value) || 0)}
                    className="w-24 ml-2 px-3 py-1 bg-[#0f0f1a] border border-[#2a2a4a] rounded focus:border-[#00ff87] focus:outline-none"
                  />
                </div>
                <div>
                  <label className="text-sm text-gray-400">Free Transfers</label>
                  <input
                    type="number"
                    min="0"
                    max="5"
                    value={freeTransfers}
                    onChange={(e) => setFreeTransfers(parseInt(e.target.value) || 1)}
                    className="w-16 ml-2 px-3 py-1 bg-[#0f0f1a] border border-[#2a2a4a] rounded focus:border-[#00ff87] focus:outline-none"
                  />
                </div>
                <button
                  onClick={getTransferSuggestions}
                  disabled={mySquad.length < 11 || transferLoading}
                  className="btn btn-primary ml-auto"
                >
                  {transferLoading ? (
                    <RefreshCw className="w-4 h-4 animate-spin" />
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
                  Top 3 Transfer Suggestions
                </div>
                
                <div className="space-y-4">
                  {transferSuggestions.map((suggestion, i) => (
                    <div key={i} className="p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-lg font-bold text-[#00ff87]">#{i + 1}</span>
                        <span className={`px-2 py-1 rounded text-sm font-medium ${
                          suggestion.points_gain > 0 ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                        }`}>
                          {suggestion.points_gain > 0 ? '+' : ''}{suggestion.points_gain} pts
                        </span>
                      </div>
                      
                      <div className="flex items-center gap-4">
                        {/* Out */}
                        <div className="flex-1 p-3 bg-red-500/10 rounded-lg border border-red-500/30">
                          <div className="text-xs text-red-400 mb-1">Transfer Out</div>
                          <div className="font-medium">{suggestion.out.name}</div>
                          <div className="text-sm text-gray-400">{suggestion.out.team} • £{suggestion.out.price}m</div>
                          <div className="text-xs text-gray-500 mt-1">
                            vs {suggestion.out.fixture} (FDR {suggestion.out.fixture_difficulty}) • Form: {suggestion.out.form}
                          </div>
                        </div>
                        
                        <ArrowRightLeft className="w-6 h-6 text-gray-500" />
                        
                        {/* In */}
                        <div className="flex-1 p-3 bg-green-500/10 rounded-lg border border-green-500/30">
                          <div className="text-xs text-green-400 mb-1">Transfer In</div>
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{suggestion.in.name}</span>
                            {suggestion.in.european_comp && (
                              <span className="px-1 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-400">
                                {suggestion.in.european_comp}
                              </span>
                            )}
                          </div>
                          <div className="text-sm text-gray-400">{suggestion.in.team} • £{suggestion.in.price}m</div>
                          <div className="text-xs text-gray-500 mt-1">
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
                          <td className="py-2 text-right font-mono">{player.predicted}</td>
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

        {/* Top Picks Tab */}
        {activeTab === 'picks' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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
                          <div className="text-[#00ff87] font-mono font-semibold">{player.predicted_points}</div>
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
                      <td className="py-3 text-right font-mono text-[#00ff87] font-semibold">{player.predicted_points}</td>
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
