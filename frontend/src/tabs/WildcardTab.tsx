import React, { useState, useEffect, useRef } from 'react'
import { 
  Sparkles, RefreshCw, TrendingUp, Users, Target, Calendar,
  ChevronDown, ChevronUp, Home, Plane, Crown, Star
} from 'lucide-react'
import type { WildcardTrajectory, TrajectoryPlayer, GameweekBreakdown } from '../types'
import { submitWildcardTrajectory, getWildcardTrajectoryResult } from '../api/wildcard'
import { fetchTask } from '../api/tasks'

interface WildcardTabProps {
  gameweek: number | null
}

const WildcardTab: React.FC<WildcardTabProps> = ({ gameweek }) => {
  const [loading, setLoading] = useState(false)
  const [trajectory, setTrajectory] = useState<WildcardTrajectory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [budget, setBudget] = useState(100.0)
  const [horizon, setHorizon] = useState(8)
  const [selectedGw, setSelectedGw] = useState<number | null>(null)
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['squad', 'fixtures']))
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null)
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null)

  const toggleSection = (section: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev)
      if (next.has(section)) next.delete(section)
      else next.add(section)
      return next
    })
  }

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current)
      }
    }
  }, [])

  const fetchTrajectory = async () => {
    // Don't allow multiple requests
    if (loading) {
      return
    }
    
    setLoading(true)
    setError(null)
    // Don't clear trajectory - keep previous one visible while loading new one
    
    // Clear any existing polling
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current)
      pollingIntervalRef.current = null
    }
    
    try {
      // Submit task and get task ID
      const taskResponse = await submitWildcardTrajectory({ budget, horizon })
      const taskId = taskResponse.task_id
      setCurrentTaskId(taskId)
      
      // Start polling for task completion
      const pollForResult = async () => {
        try {
          const task = await fetchTask(taskId)
          
          if (task.status === 'completed') {
            // Task completed - fetch result
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current)
              pollingIntervalRef.current = null
            }
            
            try {
              const data = await getWildcardTrajectoryResult(taskId)
              setTrajectory(data)
              // Set first gameweek as selected
              const gws = Object.keys(data.gameweek_predictions).map(Number).sort((a, b) => a - b)
              if (gws.length > 0) setSelectedGw(gws[0])
              setLoading(false)
              setCurrentTaskId(null)
            } catch (err: any) {
              setError(err.message || 'Failed to fetch trajectory result')
              setLoading(false)
              setCurrentTaskId(null)
            }
          } else if (task.status === 'failed') {
            // Task failed
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current)
              pollingIntervalRef.current = null
            }
            setError(task.error || 'Failed to generate trajectory')
            setLoading(false)
            setCurrentTaskId(null)
          }
          // If still running or pending, continue polling
        } catch (err) {
          // Error fetching task - continue polling
          console.error('Error polling task status:', err)
        }
      }
      
      // Poll immediately, then every 2 seconds
      pollForResult()
      pollingIntervalRef.current = setInterval(pollForResult, 2000)
      
    } catch (err: any) {
      setError(err.message || 'Failed to submit trajectory calculation')
      setLoading(false)
      setCurrentTaskId(null)
    }
  }

  const getFdrColor = (fdr: number) => {
    if (fdr <= 2) return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
    if (fdr === 3) return 'bg-amber-500/20 text-amber-400 border-amber-500/30'
    return 'bg-rose-500/20 text-rose-400 border-rose-500/30'
  }

  const getFdrBg = (fdr: number) => {
    if (fdr <= 2) return 'bg-emerald-500'
    if (fdr === 3) return 'bg-amber-500'
    return 'bg-rose-500'
  }

  const getPositionColor = (pos: string) => {
    switch (pos) {
      case 'GK': return 'bg-amber-500/20 text-amber-400'
      case 'DEF': return 'bg-sky-500/20 text-sky-400'
      case 'MID': return 'bg-emerald-500/20 text-emerald-400'
      case 'FWD': return 'bg-rose-500/20 text-rose-400'
      default: return 'bg-slate-500/20 text-slate-400'
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

  const parseFormation = (formation: string) => {
    const parts = formation.split('-').map(Number)
    return {
      def: parts[0] || 0,
      mid: parts[1] || 0,
      fwd: parts[2] || 0,
      gk: 1
    }
  }

  const renderPlayerPill = (player: TrajectoryPlayer, isCaptain: boolean = false, isViceCaptain: boolean = false) => {
    const pillClasses = "flex flex-col items-center justify-center p-2 sm:p-3 rounded-lg border-2 w-[90px] sm:w-[110px] h-[100px] sm:h-[120px] transition-all"
    
    return (
      <div
        className={`${pillClasses} ${
          isCaptain 
            ? 'bg-yellow-500/30 border-yellow-400 shadow-lg shadow-yellow-500/20' 
            : isViceCaptain
            ? 'bg-purple-500/30 border-purple-400 shadow-lg shadow-purple-500/20'
            : 'bg-slate-800/80 border-slate-700'
        }`}
      >
        <div className="flex items-center gap-1 mb-1 flex-wrap justify-center">
          {isCaptain && <span className="text-yellow-400 font-bold text-[10px]">©</span>}
          {isViceCaptain && <span className="text-purple-400 font-bold text-[10px]">V</span>}
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${getPositionClass(player.position)}`}>
            {player.position}
          </span>
        </div>
        <div className="font-medium text-[11px] sm:text-xs text-center truncate w-full leading-tight">{player.name}</div>
        <div className="text-[9px] text-slate-400 truncate w-full text-center mt-0.5">{player.team}</div>
        <div className="text-[9px] text-violet-400 font-mono mt-1">{player.predicted_points.toFixed(1)}</div>
      </div>
    )
  }

  const renderWildcardPitch = () => {
    if (!trajectory) return null

    const formationLayout = parseFormation(trajectory.formation)
    
    // Group players by position
    const byPosition = {
      GK: trajectory.squad.filter(p => p.position === 'GK'),
      DEF: trajectory.squad.filter(p => p.position === 'DEF'),
      MID: trajectory.squad.filter(p => p.position === 'MID'),
      FWD: trajectory.squad.filter(p => p.position === 'FWD'),
    }

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

    const isCaptain = (player: TrajectoryPlayer) => player.id === trajectory.captain.id
    const isViceCaptain = (player: TrajectoryPlayer) => player.id === trajectory.vice_captain.id

    return (
      <div className="bg-gradient-to-b from-green-900/20 via-green-800/10 to-green-900/20 rounded-lg border border-green-500/20 p-3 sm:p-4 md:p-6">
        <div className="relative min-h-[400px] sm:min-h-[500px] md:min-h-[550px] flex flex-col justify-between">
          {/* Goalkeeper (TOP) */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4">
            {startingXiGK.map((player) => (
              <div key={`gk-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
            {/* Bench GK */}
            {benchGK.map((player) => (
              <div key={`bench-gk-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
          </div>

          {/* Defenders */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
            {startingXiDEF.map((player) => (
              <div key={`def-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
            {/* Bench DEF */}
            {benchDEF.map((player) => (
              <div key={`bench-def-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
          </div>

          {/* Midfielders */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
            {startingXiMID.map((player) => (
              <div key={`mid-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
            {/* Bench MID */}
            {benchMID.map((player) => (
              <div key={`bench-mid-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
          </div>

          {/* Forwards (BOTTOM) */}
          <div className="flex justify-center items-center gap-2 sm:gap-3 flex-wrap">
            {startingXiFWD.map((player) => (
              <div key={`fwd-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
            {/* Bench FWD */}
            {benchFWD.map((player) => (
              <div key={`bench-fwd-${player.id}`}>
                {renderPlayerPill(player, isCaptain(player), isViceCaptain(player))}
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  const renderPlayerCard = (player: TrajectoryPlayer, isCaptain: boolean = false, isViceCaptain: boolean = false) => (
    <div 
      key={player.id}
      className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 hover:border-violet-500/30 transition-all"
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${getPositionColor(player.position)}`}>
            {player.position}
          </span>
          <span className="text-white font-medium text-sm">{player.name}</span>
          {isCaptain && <Crown className="w-4 h-4 text-amber-400" />}
          {isViceCaptain && <Star className="w-4 h-4 text-slate-400" />}
        </div>
        <span className="text-slate-400 text-xs">£{player.price.toFixed(1)}m</span>
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-500">{player.team}</span>
        <div className="flex items-center gap-2">
          <span className="text-violet-400 font-medium">{player.predicted_points.toFixed(1)} pts</span>
          <span className={`px-1.5 py-0.5 rounded text-xs ${getFdrColor(Math.round(player.avg_fdr))}`}>
            FDR {player.avg_fdr.toFixed(1)}
          </span>
        </div>
      </div>
    </div>
  )

  const renderGameweekTimeline = () => {
    if (!trajectory) return null
    const gws = Object.keys(trajectory.gameweek_predictions).map(Number).sort((a, b) => a - b)
    
    return (
      <div className="flex gap-1 overflow-x-auto pb-2">
        {gws.map(gw => {
          const gwData = trajectory.gameweek_predictions[gw]
          const isSelected = selectedGw === gw
          return (
            <button
              key={gw}
              onClick={() => setSelectedGw(gw)}
              className={`flex-shrink-0 px-3 py-2 rounded-lg transition-all ${
                isSelected 
                  ? 'bg-violet-600 text-white' 
                  : 'bg-slate-800/50 text-slate-400 hover:bg-slate-700/50'
              }`}
            >
              <div className="text-xs font-medium">GW{gw}</div>
              <div className="text-sm font-bold">{gwData.predicted_points.toFixed(0)}</div>
            </button>
          )
        })}
      </div>
    )
  }

  const renderGameweekDetail = () => {
    if (!trajectory || selectedGw === null) return null
    const gwData = trajectory.gameweek_predictions[selectedGw]
    if (!gwData) return null

    return (
      <div className="bg-slate-800/30 rounded-xl p-4 border border-slate-700/50">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Calendar className="w-5 h-5 text-violet-400" />
            <span className="text-white font-semibold">Gameweek {selectedGw}</span>
            <span className="text-slate-500">({gwData.formation})</span>
          </div>
          <div className="text-violet-400 font-bold text-lg">{gwData.predicted_points.toFixed(1)} pts</div>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {gwData.starting_xi.map((player) => (
            <div 
              key={player.id}
              className="bg-slate-900/50 rounded-lg p-3 border border-slate-700/30"
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className={`px-1.5 py-0.5 rounded text-xs ${getPositionColor(player.position)}`}>
                    {player.position}
                  </span>
                  <span className="text-white text-sm font-medium">{player.name}</span>
                </div>
                <span className="text-violet-400 font-medium">{player.predicted.toFixed(1)}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-1 text-slate-400">
                  {player.is_home ? <Home className="w-3 h-3" /> : <Plane className="w-3 h-3" />}
                  <span>vs {player.opponent}</span>
                </div>
                <span className={`px-1.5 py-0.5 rounded ${getFdrColor(player.fdr)}`}>
                  FDR {player.fdr}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const renderFixtureBlocks = () => {
    if (!trajectory || trajectory.fixture_blocks.length === 0) return null

    return (
      <div className="space-y-3">
        {trajectory.fixture_blocks.slice(0, 5).map((block, idx) => (
          <div key={idx} className="bg-slate-800/30 rounded-lg p-3 border border-slate-700/30">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="text-white font-medium">{block.team}</span>
                <span className="text-slate-500 text-xs">({block.players.join(', ')})</span>
              </div>
              <span className={`px-2 py-0.5 rounded text-xs ${getFdrColor(Math.round(block.avg_fdr))}`}>
                Avg FDR {block.avg_fdr}
              </span>
            </div>
            {block.green_runs.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {block.green_runs.map((run, runIdx) => (
                  <div key={runIdx} className="flex items-center gap-0.5">
                    {run.map((fixture, fixIdx) => (
                      <div 
                        key={fixIdx}
                        className={`${getFdrBg(fixture.fdr)} px-2 py-1 text-xs text-white rounded ${
                          fixIdx === 0 ? 'rounded-l-lg' : ''
                        } ${fixIdx === run.length - 1 ? 'rounded-r-lg' : ''}`}
                        title={`${fixture.opponent} (${fixture.is_home ? 'H' : 'A'})`}
                      >
                        {fixture.gw}
                      </div>
                    ))}
                    <span className="text-slate-500 text-xs mx-1">
                      GW{run[0].gw}-{run[run.length - 1].gw}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-gradient-to-br from-violet-600/20 to-fuchsia-600/20 rounded-2xl p-6 border border-violet-500/20">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 bg-violet-500/20 rounded-xl">
            <Sparkles className="w-6 h-6 text-violet-400" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">Wildcard Trajectory Optimizer</h2>
            <p className="text-slate-400 text-sm">
              8-GW squad path using hybrid LSTM-XGBoost model
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Budget (£m)</label>
            <input
              type="number"
              value={budget}
              onChange={(e) => setBudget(parseFloat(e.target.value) || 100)}
              className="w-24 px-3 py-2 bg-slate-800/50 border border-slate-700 rounded-lg text-white text-sm focus:outline-none focus:border-violet-500"
              step="0.1"
              min="50"
              max="120"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Horizon (GWs)</label>
            <input
              type="number"
              value={horizon}
              onChange={(e) => setHorizon(parseInt(e.target.value) || 8)}
              className="w-20 px-3 py-2 bg-slate-800/50 border border-slate-700 rounded-lg text-white text-sm focus:outline-none focus:border-violet-500"
              min="3"
              max="10"
            />
          </div>
          <button
            onClick={fetchTrajectory}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:bg-violet-600/50 text-white rounded-lg transition-colors"
          >
            {loading ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <Sparkles className="w-4 h-4" />
            )}
            <span>{loading ? 'Optimizing...' : 'Generate Trajectory'}</span>
          </button>
        </div>

        {error && (
          <div className="mt-4 p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-rose-400 text-sm">
            {error}
          </div>
        )}
      </div>

      {/* Results */}
      {trajectory && (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
              <div className="text-slate-400 text-xs mb-1">Total Predicted</div>
              <div className="text-2xl font-bold text-violet-400">{trajectory.total_predicted_points.toFixed(0)}</div>
              <div className="text-slate-500 text-xs">over {trajectory.horizon} GWs</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
              <div className="text-slate-400 text-xs mb-1">Avg Weekly</div>
              <div className="text-2xl font-bold text-emerald-400">{trajectory.avg_weekly_points.toFixed(1)}</div>
              <div className="text-slate-500 text-xs">pts per GW</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
              <div className="text-slate-400 text-xs mb-1">Squad Cost</div>
              <div className="text-2xl font-bold text-white">£{trajectory.total_cost.toFixed(1)}m</div>
              <div className="text-slate-500 text-xs">£{trajectory.remaining_budget.toFixed(1)}m ITB</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
              <div className="text-slate-400 text-xs mb-1">Formation</div>
              <div className="text-2xl font-bold text-amber-400">{trajectory.formation}</div>
              <div className="text-slate-500 text-xs">optimal lineup</div>
            </div>
          </div>

          {/* Gameweek Timeline */}
          <div className="bg-slate-800/30 rounded-xl p-4 border border-slate-700/50">
            <div className="flex items-center gap-2 mb-3">
              <TrendingUp className="w-5 h-5 text-violet-400" />
              <span className="text-white font-semibold">Points Trajectory</span>
            </div>
            {renderGameweekTimeline()}
          </div>

          {/* Selected Gameweek Detail */}
          {renderGameweekDetail()}

          {/* Squad Section */}
          <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 overflow-hidden">
            <button
              onClick={() => toggleSection('squad')}
              className="w-full flex items-center justify-between p-4 hover:bg-slate-700/20 transition-colors"
            >
              <div className="flex items-center gap-2">
                <Users className="w-5 h-5 text-violet-400" />
                <span className="text-white font-semibold">Optimal Squad (15)</span>
              </div>
              {expandedSections.has('squad') ? (
                <ChevronUp className="w-5 h-5 text-slate-400" />
              ) : (
                <ChevronDown className="w-5 h-5 text-slate-400" />
              )}
            </button>
            {expandedSections.has('squad') && (
              <div className="p-4 pt-0">
                {renderWildcardPitch()}
              </div>
            )}
          </div>

          {/* Fixture Blocks Section */}
          <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 overflow-hidden">
            <button
              onClick={() => toggleSection('fixtures')}
              className="w-full flex items-center justify-between p-4 hover:bg-slate-700/20 transition-colors"
            >
              <div className="flex items-center gap-2">
                <Target className="w-5 h-5 text-emerald-400" />
                <span className="text-white font-semibold">Favorable Fixture Blocks</span>
              </div>
              {expandedSections.has('fixtures') ? (
                <ChevronUp className="w-5 h-5 text-slate-400" />
              ) : (
                <ChevronDown className="w-5 h-5 text-slate-400" />
              )}
            </button>
            {expandedSections.has('fixtures') && (
              <div className="p-4 pt-0">
                {renderFixtureBlocks()}
              </div>
            )}
          </div>

          {/* Rationale */}
          {trajectory.rationale && (
            <div className="bg-slate-800/30 rounded-xl p-4 border border-slate-700/50">
              <div className="flex items-center gap-2 mb-3">
                <Sparkles className="w-5 h-5 text-fuchsia-400" />
                <span className="text-white font-semibold">AI Analysis</span>
              </div>
              <div className="text-slate-300 text-sm whitespace-pre-line prose prose-invert prose-sm max-w-none">
                {trajectory.rationale.split('\n').map((line, i) => {
                  if (line.startsWith('**') && line.endsWith('**')) {
                    return <h4 key={i} className="text-violet-400 font-semibold mt-3 mb-1">{line.replace(/\*\*/g, '')}</h4>
                  }
                  if (line.startsWith('•')) {
                    return <p key={i} className="text-slate-300 ml-2">{line}</p>
                  }
                  return <p key={i}>{line}</p>
                })}
              </div>
            </div>
          )}
        </>
      )}

      {/* Empty State */}
      {!trajectory && !loading && (
        <div className="bg-slate-800/30 rounded-xl p-12 border border-slate-700/50 text-center">
          <Sparkles className="w-12 h-12 text-slate-600 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-white mb-2">Plan Your Wildcard</h3>
          <p className="text-slate-400 text-sm max-w-md mx-auto mb-4">
            Generate an optimal 8-gameweek squad trajectory using our hybrid 
            LSTM-XGBoost model. Prioritizes long-term fixture blocks over single-week peaks.
          </p>
          <div className="text-slate-500 text-xs space-y-1">
            <p>• Hybrid model: 0.7×LSTM + 0.3×XGBoost</p>
            <p>• FDR-adjusted predictions</p>
            <p>• Transfer decay for uncertainty</p>
            <p>• MILP optimizer for squad selection</p>
          </div>
        </div>
      )}
    </div>
  )
}

export default WildcardTab

