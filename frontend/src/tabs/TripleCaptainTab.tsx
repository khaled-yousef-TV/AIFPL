import React from 'react'
import { Crown, RefreshCw } from 'lucide-react'

interface TripleCaptainRec {
  player_id?: number
  player_name: string
  team: string
  position: string
  peak_gameweek: number
  peak_haul_probability: number
  peak_expected_points: number
  peak_opponent?: string
  is_double_gameweek?: boolean
  form: number
  price: number
  all_gameweeks?: { gameweek: number; opponent: string }[]
}

interface GameweekRecs {
  recommendations: TripleCaptainRec[]
  calculated_at?: string
  total_recommendations?: number
  gameweek_range?: number
}

interface TripleCaptainTabProps {
  tripleCaptainRecs: Record<number, GameweekRecs>
  selectedTcGameweekTab: number | null
  setSelectedTcGameweekTab: (gw: number) => void
  loadingTripleCaptain: boolean
  calculatingTripleCaptain: boolean
  calculateTripleCaptain: () => void
  tcCalculationMessage: { type: 'success' | 'error'; text: string } | null
}

const TripleCaptainTab: React.FC<TripleCaptainTabProps> = ({
  tripleCaptainRecs,
  selectedTcGameweekTab,
  setSelectedTcGameweekTab,
  loadingTripleCaptain,
  calculatingTripleCaptain,
  calculateTripleCaptain,
  tcCalculationMessage,
}) => {
  const sortedGameweeks = Object.keys(tripleCaptainRecs).map(Number).sort((a, b) => b - a)
  const selectedGameweek = selectedTcGameweekTab || (sortedGameweeks.length > 0 ? sortedGameweeks[0] : null)
  const currentRecs = selectedGameweek ? tripleCaptainRecs[selectedGameweek] : null

  return (
    <div className="space-y-6">
      <div className="card">
        <div className="card-header">
          <div className="flex items-center justify-between w-full">
            <div className="flex items-center gap-2">
              <Crown className="w-5 h-5 text-purple-400" />
              <span>Triple Captain Recommendations</span>
            </div>
            <button
              onClick={calculateTripleCaptain}
              disabled={calculatingTripleCaptain}
              className="flex items-center gap-2 px-3 py-1.5 bg-purple-500/10 hover:bg-purple-500/20 text-purple-400 rounded-lg border border-purple-500/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
              title="Manually calculate Triple Captain recommendations"
            >
              <RefreshCw className={`w-4 h-4 ${calculatingTripleCaptain ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">{calculatingTripleCaptain ? 'Calculating...' : 'Calculate Now'}</span>
            </button>
          </div>
        </div>

        {tcCalculationMessage && (
          <div className={`mb-4 p-3 rounded-lg border ${
            tcCalculationMessage.type === 'success' 
              ? 'bg-green-500/10 border-green-500/30 text-green-400' 
              : 'bg-red-500/10 border-red-500/30 text-red-400'
          }`}>
            <div className="flex items-center gap-2 text-sm">
              {tcCalculationMessage.type === 'success' ? (
                <span>✓ {tcCalculationMessage.text}</span>
              ) : (
                <span>✗ {tcCalculationMessage.text}</span>
              )}
            </div>
          </div>
        )}

        {loadingTripleCaptain ? (
          <div className="text-center py-8">
            <RefreshCw className="w-8 h-8 animate-spin text-[#00ff87] mx-auto mb-4" />
            <p className="text-gray-400">Loading Triple Captain recommendations...</p>
          </div>
        ) : sortedGameweeks.length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            <Crown className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No recommendations available</p>
            <p className="text-xs mt-2">
              Recommendations are calculated daily at midnight.
            </p>
          </div>
        ) : (
          <>
            {/* Gameweek Tabs */}
            <div className="border-b border-[#2a2a4a] overflow-x-auto scrollbar-hide">
              <div className="flex gap-1 min-w-max">
                {sortedGameweeks.map((gw) => (
                  <button
                    key={gw}
                    onClick={() => setSelectedTcGameweekTab(gw)}
                    className={`px-4 py-2 border-b-2 transition-colors whitespace-nowrap ${
                      selectedGameweek === gw
                        ? 'border-purple-400 text-white'
                        : 'border-transparent text-gray-400 hover:text-white'
                    }`}
                  >
                    <span className="text-sm font-medium">GW{gw}</span>
                    {tripleCaptainRecs[gw]?.calculated_at && (
                      <span className="text-xs text-gray-500 ml-2">
                        {new Date(tripleCaptainRecs[gw].calculated_at!).toLocaleDateString('en-US', { 
                          month: 'short', 
                          day: 'numeric'
                        })}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Selected Gameweek Content */}
            {currentRecs && (
              <div className="bg-[#0f0f1a] rounded-lg border border-[#2a2a4a] p-4 sm:p-6">
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6">
                  <div>
                    <h3 className="text-xl font-semibold text-purple-400 mb-1">Gameweek {selectedGameweek}</h3>
                    {currentRecs.calculated_at && (
                      <p className="text-xs text-gray-400">
                        Calculated {new Date(currentRecs.calculated_at).toLocaleString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit'
                        })}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-4 mt-2 sm:mt-0">
                    <div className="text-right">
                      <div className="text-xs text-gray-400">Total Recommendations</div>
                      <div className="text-lg font-mono font-semibold text-purple-400">
                        {currentRecs.total_recommendations || currentRecs.recommendations?.length || 0}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs text-gray-400">Gameweek Range</div>
                      <div className="text-lg font-mono">
                        {currentRecs.gameweek_range || 5} GWs
                      </div>
                    </div>
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-gray-400 text-sm border-b border-[#2a2a4a]">
                        <th className="pb-3">#</th>
                        <th className="pb-3">Player</th>
                        <th className="pb-3">Peak GW</th>
                        <th className="pb-3">Haul Prob</th>
                        <th className="pb-3">Expected Pts</th>
                        <th className="pb-3">DGW</th>
                        <th className="pb-3">Form</th>
                        <th className="pb-3 text-right">Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(currentRecs.recommendations || []).map((rec, i) => (
                        <tr key={rec.player_id || i} className="border-b border-[#2a2a4a]/50 hover:bg-[#1f1f3a] transition-colors">
                          <td className="py-3 text-gray-500 font-mono">{i + 1}</td>
                          <td className="py-3">
                            <div>
                              <div className="font-medium">{rec.player_name}</div>
                              <div className="text-sm text-gray-400">{rec.team} • {rec.position}</div>
                            </div>
                          </td>
                          <td className="py-3">
                            <div className="flex items-center gap-2">
                              <span className="px-2 py-1 rounded text-xs font-medium bg-purple-500/20 text-purple-400 border border-purple-500/30">
                                GW{rec.peak_gameweek}
                              </span>
                              {(() => {
                                const opponent = rec.peak_opponent || 
                                  rec.all_gameweeks?.find(gw => gw.gameweek === rec.peak_gameweek)?.opponent;
                                return opponent && (
                                  <span className="text-xs text-gray-300">
                                    vs {opponent}
                                  </span>
                                );
                              })()}
                            </div>
                          </td>
                          <td className="py-3">
                            <span className="font-mono font-semibold text-purple-400">
                              {(rec.peak_haul_probability * 100).toFixed(1)}%
                            </span>
                          </td>
                          <td className="py-3">
                            <span className="font-mono text-[#00ff87]">
                              {rec.peak_expected_points.toFixed(1)}
                            </span>
                          </td>
                          <td className="py-3">
                            {rec.is_double_gameweek ? (
                              <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
                                DGW
                              </span>
                            ) : (
                              <span className="text-gray-500 text-xs">—</span>
                            )}
                          </td>
                          <td className="py-3">
                            <span className="text-sm">{rec.form.toFixed(1)}</span>
                          </td>
                          <td className="py-3 text-right font-mono text-sm">£{rec.price.toFixed(1)}m</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default TripleCaptainTab

