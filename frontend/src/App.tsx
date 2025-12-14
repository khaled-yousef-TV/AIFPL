import { useState, useEffect } from 'react'
import { 
  Users, TrendingUp, RefreshCw, Zap, Award, 
  ChevronRight, Star, Target, Flame, AlertTriangle, Plane
} from 'lucide-react'

const API_BASE = 'http://localhost:8001'

// Types
interface Player {
  id: number
  name: string
  full_name?: string
  team: string
  position: string
  position_id: number
  price: number
  predicted: number
  predicted_points?: number
  form: number
  total_points: number
  ownership: number
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

function App() {
  const [loading, setLoading] = useState(true)
  const [squad, setSquad] = useState<SuggestedSquad | null>(null)
  const [topPicks, setTopPicks] = useState<Record<string, Player[]>>({})
  const [differentials, setDifferentials] = useState<Player[]>([])
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [activeTab, setActiveTab] = useState('squad')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    setError(null)
    
    try {
      const [gwRes, squadRes, topsRes, diffsRes] = await Promise.all([
        fetch(`${API_BASE}/api/gameweek`).then(r => r.json()),
        fetch(`${API_BASE}/api/suggested-squad`).then(r => r.json()),
        fetch(`${API_BASE}/api/top-picks`).then(r => r.json()),
        fetch(`${API_BASE}/api/differentials`).then(r => r.json()),
      ])
      
      setGameweek(gwRes)
      setSquad(squadRes)
      setTopPicks(topsRes)
      setDifferentials(diffsRes.differentials || [])
    } catch (err: any) {
      setError(err.message || 'Failed to load data')
      console.error('Load error:', err)
    } finally {
      setLoading(false)
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
          <button onClick={loadData} className="btn btn-primary">
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
          
          <button onClick={loadData} className="btn btn-secondary flex items-center gap-2">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </header>

      {/* Navigation */}
      <nav className="bg-[#1a1a2e]/50 border-b border-[#2a2a4a] px-6">
        <div className="max-w-6xl mx-auto flex gap-1">
          {[
            { id: 'squad', icon: Users, label: 'Suggested Squad' },
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
        
        {/* Suggested Squad Tab */}
        {activeTab === 'squad' && squad && (
          <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Formation</div>
                <div className="text-2xl font-bold text-[#00ff87]">{squad.formation}</div>
              </div>
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Predicted Points</div>
                <div className="text-2xl font-bold text-[#00ff87]">{squad.predicted_points}</div>
              </div>
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Squad Cost</div>
                <div className="text-2xl font-bold">£{squad.total_cost}m</div>
              </div>
              <div className="card">
                <div className="text-gray-400 text-sm mb-1">Remaining</div>
                <div className="text-2xl font-bold text-green-400">£{squad.remaining_budget}m</div>
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
                    <span className="font-semibold text-lg">{squad.captain.name}</span>
                  </div>
                  <span className="text-gray-400">Predicted: <span className="text-[#00ff87] font-mono">{squad.captain.predicted} × 2 = {(squad.captain.predicted * 2).toFixed(1)}</span></span>
                </div>
                <div className="flex-1 p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-gray-400">V</span>
                    <span className="font-medium">{squad.vice_captain.name}</span>
                  </div>
                  <span className="text-gray-400">Predicted: <span className="font-mono">{squad.vice_captain.predicted}</span></span>
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
                    {squad.starting_xi.map((player: any, i) => (
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
                {squad.bench.map((player: any, i) => (
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
