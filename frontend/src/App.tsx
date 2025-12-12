import { useState, useEffect } from 'react'
import { 
  Users, TrendingUp, RefreshCw, Settings, LogOut, 
  Clock, Target, Zap, Award, AlertCircle, CheckCircle,
  ChevronRight, BarChart3
} from 'lucide-react'
import { api } from './api/client'

// Types
interface Player {
  id: number
  name: string
  team: number
  position: string
  price: number
  points?: number
  predicted_points?: number
  form?: number
  ownership?: number
  is_captain?: boolean
  is_vice_captain?: boolean
  is_starter?: boolean
}

interface Recommendation {
  captain?: {
    id: number
    name: string
    predicted: number
  }
  vice_captain?: {
    id: number
    name: string
    predicted: number
  }
  reasoning?: string
}

interface GameWeekInfo {
  current?: { id: number; name: string }
  next?: { id: number; name: string; deadline: string }
}

function App() {
  const [authenticated, setAuthenticated] = useState(false)
  const [loading, setLoading] = useState(true)
  const [team, setTeam] = useState<Player[]>([])
  const [predictions, setPredictions] = useState<Player[]>([])
  const [captainRec, setCaptainRec] = useState<Recommendation | null>(null)
  const [gameweek, setGameweek] = useState<GameWeekInfo | null>(null)
  const [activeTab, setActiveTab] = useState('dashboard')
  
  // Login state
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loginError, setLoginError] = useState('')

  useEffect(() => {
    checkAuth()
  }, [])

  const checkAuth = async () => {
    try {
      const status = await api.getAuthStatus()
      setAuthenticated(status.authenticated)
      if (status.authenticated) {
        loadDashboardData()
      }
    } catch (error) {
      console.error('Auth check failed:', error)
    } finally {
      setLoading(false)
    }
  }

  const loadDashboardData = async () => {
    try {
      const [teamData, gwData] = await Promise.all([
        api.getCurrentTeam().catch(() => ({ players: [] })),
        api.getGameweek().catch(() => null)
      ])
      
      setTeam(teamData.players || [])
      setGameweek(gwData)
      
      // Load recommendations
      const captain = await api.getCaptainRecommendation().catch(() => null)
      setCaptainRec(captain)
      
      // Load predictions
      const preds = await api.getPredictions().catch(() => ({ predictions: [] }))
      setPredictions(preds.predictions || [])
    } catch (error) {
      console.error('Failed to load data:', error)
    }
  }

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginError('')
    
    try {
      await api.login(email, password)
      setAuthenticated(true)
      loadDashboardData()
    } catch (error: any) {
      setLoginError(error.message || 'Login failed')
    }
  }

  const handleLogout = async () => {
    await api.logout()
    setAuthenticated(false)
    setTeam([])
    setPredictions([])
  }

  const getPositionClass = (pos: string) => {
    const classes: Record<string, string> = {
      'GK': 'pos-gk',
      'DEF': 'pos-def',
      'MID': 'pos-mid',
      'FWD': 'pos-fwd'
    }
    return classes[pos] || ''
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
      <div className="min-h-screen flex items-center justify-center">
        <RefreshCw className="w-8 h-8 animate-spin text-[#00ff87]" />
      </div>
    )
  }

  // Login Screen
  if (!authenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <div className="card max-w-md w-full">
          <div className="text-center mb-8">
            <div className="w-16 h-16 bg-gradient-to-br from-[#00ff87] to-[#00cc6a] rounded-xl mx-auto mb-4 flex items-center justify-center">
              <Zap className="w-8 h-8 text-[#0f0f1a]" />
            </div>
            <h1 className="text-2xl font-bold">FPL AI Agent</h1>
            <p className="text-gray-400 mt-2">Sign in with your FPL account</p>
          </div>
          
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-sm text-gray-400 mb-2">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg px-4 py-3 focus:outline-none focus:border-[#00ff87]"
                placeholder="your@email.com"
                required
              />
            </div>
            
            <div>
              <label className="block text-sm text-gray-400 mb-2">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg px-4 py-3 focus:outline-none focus:border-[#00ff87]"
                placeholder="••••••••"
                required
              />
            </div>
            
            {loginError && (
              <div className="flex items-center gap-2 text-red-400 text-sm">
                <AlertCircle className="w-4 h-4" />
                {loginError}
              </div>
            )}
            
            <button type="submit" className="btn btn-primary w-full py-3">
              Sign In to FPL
            </button>
          </form>
          
          <p className="text-center text-gray-500 text-sm mt-6">
            Uses official FPL credentials. Your data stays private.
          </p>
        </div>
      </div>
    )
  }

  // Main Dashboard
  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="bg-[#1a1a2e] border-b border-[#2a2a4a] px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-[#00ff87] to-[#00cc6a] rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-[#0f0f1a]" />
            </div>
            <div>
              <h1 className="font-bold text-lg">FPL AI Agent</h1>
              <p className="text-xs text-gray-400">
                {gameweek?.next ? `GW${gameweek.next.id} • ${formatDeadline(gameweek.next.deadline)}` : 'Loading...'}
              </p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <button onClick={loadDashboardData} className="btn btn-secondary flex items-center gap-2">
              <RefreshCw className="w-4 h-4" />
              Refresh
            </button>
            <button onClick={handleLogout} className="text-gray-400 hover:text-white">
              <LogOut className="w-5 h-5" />
            </button>
          </div>
        </div>
      </header>

      {/* Navigation */}
      <nav className="bg-[#1a1a2e]/50 border-b border-[#2a2a4a] px-6">
        <div className="max-w-7xl mx-auto flex gap-1">
          {[
            { id: 'dashboard', icon: BarChart3, label: 'Dashboard' },
            { id: 'team', icon: Users, label: 'My Team' },
            { id: 'predictions', icon: TrendingUp, label: 'Predictions' },
            { id: 'settings', icon: Settings, label: 'Settings' },
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
      <main className="max-w-7xl mx-auto p-6">
        {activeTab === 'dashboard' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Status Card */}
            <div className="card">
              <div className="card-header">
                <Clock className="w-5 h-5 text-[#00ff87]" />
                Gameweek Status
              </div>
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">Current GW</span>
                  <span className="font-mono">{gameweek?.current?.id || '-'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">Next Deadline</span>
                  <span className="font-mono text-sm">{formatDeadline(gameweek?.next?.deadline)}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">Agent Status</span>
                  <span className="badge badge-success flex items-center gap-1">
                    <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span>
                    Active
                  </span>
                </div>
              </div>
            </div>

            {/* Captain Recommendation */}
            <div className="card">
              <div className="card-header">
                <Award className="w-5 h-5 text-yellow-400" />
                Captain Pick
              </div>
              {captainRec?.captain ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-3 bg-yellow-500/10 rounded-lg border border-yellow-500/30">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-yellow-400 text-lg">©</span>
                        <span className="font-semibold">{captainRec.captain.name}</span>
                      </div>
                      <span className="text-sm text-gray-400">Predicted: {captainRec.captain.predicted} pts</span>
                    </div>
                    <ChevronRight className="w-5 h-5 text-gray-500" />
                  </div>
                  
                  {captainRec.vice_captain && (
                    <div className="flex items-center justify-between p-3 bg-[#0f0f1a] rounded-lg">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-gray-400">V</span>
                          <span>{captainRec.vice_captain.name}</span>
                        </div>
                        <span className="text-sm text-gray-400">Predicted: {captainRec.vice_captain.predicted} pts</span>
                      </div>
                    </div>
                  )}
                  
                  <p className="text-sm text-gray-400">{captainRec.reasoning}</p>
                </div>
              ) : (
                <p className="text-gray-400">Loading recommendations...</p>
              )}
            </div>

            {/* Quick Stats */}
            <div className="card">
              <div className="card-header">
                <Target className="w-5 h-5 text-blue-400" />
                Team Overview
              </div>
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">Squad Value</span>
                  <span className="font-mono">
                    £{team.reduce((sum, p) => sum + (p.price || 0), 0).toFixed(1)}m
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">Total Points</span>
                  <span className="font-mono">{team.reduce((sum, p) => sum + (p.points || 0), 0)}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-400">Avg Form</span>
                  <span className="font-mono">
                    {team.length > 0 
                      ? (team.reduce((sum, p) => sum + (p.form || 0), 0) / team.length).toFixed(1)
                      : '-'
                    }
                  </span>
                </div>
              </div>
            </div>

            {/* Team Table */}
            <div className="card lg:col-span-2">
              <div className="card-header">
                <Users className="w-5 h-5 text-[#00ff87]" />
                Starting XI
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-gray-400 text-sm border-b border-[#2a2a4a]">
                      <th className="pb-3">Player</th>
                      <th className="pb-3">Pos</th>
                      <th className="pb-3">Price</th>
                      <th className="pb-3">Form</th>
                      <th className="pb-3">Points</th>
                    </tr>
                  </thead>
                  <tbody>
                    {team.filter(p => p.is_starter).map(player => (
                      <tr key={player.id} className="table-row">
                        <td className="py-3">
                          <div className="flex items-center gap-2">
                            {player.is_captain && <span className="text-yellow-400">©</span>}
                            {player.is_vice_captain && <span className="text-gray-400">V</span>}
                            <span className="font-medium">{player.name}</span>
                          </div>
                        </td>
                        <td className={`py-3 ${getPositionClass(player.position)}`}>
                          {player.position}
                        </td>
                        <td className="py-3 font-mono">£{player.price}m</td>
                        <td className="py-3 font-mono">{player.form}</td>
                        <td className="py-3 font-mono">{player.points}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Top Predictions */}
            <div className="card">
              <div className="card-header">
                <TrendingUp className="w-5 h-5 text-purple-400" />
                Top Predictions
              </div>
              <div className="space-y-2">
                {predictions.slice(0, 8).map((player, i) => (
                  <div key={player.id} className="flex items-center justify-between py-2">
                    <div className="flex items-center gap-3">
                      <span className="text-gray-500 font-mono w-4">{i + 1}</span>
                      <span className={`text-xs ${getPositionClass(player.position)}`}>
                        {player.position}
                      </span>
                      <span>{player.name}</span>
                    </div>
                    <span className="font-mono text-[#00ff87]">
                      {player.predicted_points?.toFixed(1)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'team' && (
          <div className="card">
            <div className="card-header">
              <Users className="w-5 h-5 text-[#00ff87]" />
              Full Squad
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-gray-400 text-sm border-b border-[#2a2a4a]">
                    <th className="pb-3">Player</th>
                    <th className="pb-3">Position</th>
                    <th className="pb-3">Price</th>
                    <th className="pb-3">Form</th>
                    <th className="pb-3">Points</th>
                    <th className="pb-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {team.map(player => (
                    <tr key={player.id} className="table-row">
                      <td className="py-3">
                        <div className="flex items-center gap-2">
                          {player.is_captain && <span className="text-yellow-400">©</span>}
                          {player.is_vice_captain && <span className="text-gray-400">V</span>}
                          <span className="font-medium">{player.name}</span>
                        </div>
                      </td>
                      <td className={`py-3 ${getPositionClass(player.position)}`}>
                        {player.position}
                      </td>
                      <td className="py-3 font-mono">£{player.price}m</td>
                      <td className="py-3 font-mono">{player.form}</td>
                      <td className="py-3 font-mono">{player.points}</td>
                      <td className="py-3">
                        <span className={`badge ${player.is_starter ? 'badge-success' : 'badge-warning'}`}>
                          {player.is_starter ? 'Starting' : `Bench ${player.bench_order}`}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {activeTab === 'predictions' && (
          <div className="card">
            <div className="card-header">
              <TrendingUp className="w-5 h-5 text-purple-400" />
              Player Predictions
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-gray-400 text-sm border-b border-[#2a2a4a]">
                    <th className="pb-3">#</th>
                    <th className="pb-3">Player</th>
                    <th className="pb-3">Position</th>
                    <th className="pb-3">Price</th>
                    <th className="pb-3">Form</th>
                    <th className="pb-3">Ownership</th>
                    <th className="pb-3">Predicted</th>
                  </tr>
                </thead>
                <tbody>
                  {predictions.map((player, i) => (
                    <tr key={player.id} className="table-row">
                      <td className="py-3 text-gray-500 font-mono">{i + 1}</td>
                      <td className="py-3 font-medium">{player.name}</td>
                      <td className={`py-3 ${getPositionClass(player.position)}`}>
                        {player.position}
                      </td>
                      <td className="py-3 font-mono">£{player.price}m</td>
                      <td className="py-3 font-mono">{player.form}</td>
                      <td className="py-3 font-mono">{player.ownership}%</td>
                      <td className="py-3 font-mono text-[#00ff87]">
                        {player.predicted_points?.toFixed(1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {activeTab === 'settings' && (
          <div className="card max-w-2xl">
            <div className="card-header">
              <Settings className="w-5 h-5 text-gray-400" />
              Agent Settings
            </div>
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">Auto-Execute Decisions</div>
                  <div className="text-sm text-gray-400">Automatically apply recommended changes</div>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input type="checkbox" className="sr-only peer" />
                  <div className="w-11 h-6 bg-[#2a2a4a] rounded-full peer peer-checked:bg-[#00ff87] peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all"></div>
                </label>
              </div>
              
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">Differential Mode</div>
                  <div className="text-sm text-gray-400">Prefer low-ownership picks for rank climbing</div>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input type="checkbox" className="sr-only peer" />
                  <div className="w-11 h-6 bg-[#2a2a4a] rounded-full peer peer-checked:bg-[#00ff87] peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all"></div>
                </label>
              </div>
              
              <div className="pt-4 border-t border-[#2a2a4a]">
                <div className="font-medium mb-2">Account</div>
                <button onClick={handleLogout} className="btn btn-secondary text-red-400">
                  Sign Out
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default App

