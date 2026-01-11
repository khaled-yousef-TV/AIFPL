import React from 'react'
import { Trophy, RefreshCw } from 'lucide-react'
import type { SelectedTeam, Player } from '../types'

interface SelectedTeamsTabProps {
  selectedTeams: Record<number, SelectedTeam>
  selectedGameweekTab: number | null
  setSelectedGameweekTab: (gw: number) => void
  updateDailySnapshot: () => void
  isTaskRunning: (taskType: string) => boolean
  snapshotUpdateMessage: { type: 'success' | 'error'; text: string } | null
  getPositionClass: (position: string) => string
  renderPitchFormation: (startingXI: Player[], formation: string) => React.ReactNode
}

const SelectedTeamsTab: React.FC<SelectedTeamsTabProps> = ({
  selectedTeams,
  selectedGameweekTab,
  setSelectedGameweekTab,
  updateDailySnapshot,
  isTaskRunning,
  snapshotUpdateMessage,
  getPositionClass,
  renderPitchFormation,
}) => {
  const sortedTeams = Object.values(selectedTeams).sort((a, b) => b.gameweek - a.gameweek)
  const selectedGameweek = selectedGameweekTab || (sortedTeams.length > 0 ? sortedTeams[0].gameweek : null)
  const currentTeam = selectedGameweek ? selectedTeams[selectedGameweek] : null

  return (
    <div className="space-y-6">
      <div className="card">
        <div className="card-header">
          <div className="flex items-center justify-between w-full">
            <div className="flex items-center gap-2">
              <Trophy className="w-5 h-5 text-[#00ff87]" />
              <span>Free Hit of the Week</span>
            </div>
            <button
              onClick={updateDailySnapshot}
              disabled={isTaskRunning('daily_snapshot')}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#00ff87]/10 hover:bg-[#00ff87]/20 text-[#00ff87] rounded-lg border border-[#00ff87]/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium"
              title="Refresh free hit team with latest player status"
            >
              <RefreshCw className={`w-4 h-4 ${isTaskRunning('daily_snapshot') ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">{isTaskRunning('daily_snapshot') ? 'Updating...' : 'Refresh Now'}</span>
            </button>
          </div>
        </div>

        {snapshotUpdateMessage && (
          <div className={`mb-4 p-3 rounded-lg border ${
            snapshotUpdateMessage.type === 'success' 
              ? 'bg-green-500/10 border-green-500/30 text-green-400' 
              : 'bg-red-500/10 border-red-500/30 text-red-400'
          }`}>
            <div className="flex items-center gap-2 text-sm">
              {snapshotUpdateMessage.type === 'success' ? (
                <span>✓ {snapshotUpdateMessage.text}</span>
              ) : (
                <span>✗ {snapshotUpdateMessage.text}</span>
              )}
            </div>
          </div>
        )}

        <p className="text-gray-400 text-sm mb-4">
          View your saved suggested squads. Squads are automatically saved daily at midnight and 30 minutes before each gameweek deadline.
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
                      {new Date(team.saved_at).toLocaleDateString('en-US', { 
                        month: 'short', 
                        day: 'numeric'
                      })}
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
                      Saved {new Date(currentTeam.saved_at).toLocaleString('en-US', {
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
                    <h4 className="text-sm text-gray-400 mb-3 uppercase font-semibold">
                      Starting XI • {currentTeam.squad.formation}
                    </h4>
                    {renderPitchFormation(currentTeam.squad.starting_xi, currentTeam.squad.formation)}
                  </div>

                  {/* Bench */}
                  <div>
                    <h4 className="text-sm text-gray-400 mb-3 uppercase font-semibold">Bench</h4>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                      {[...currentTeam.squad.bench].sort((a, b) => {
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

                    {/* Captain Info */}
                    <div className="mt-4 pt-4 border-t border-[#2a2a4a]">
                      {(() => {
                        const captainPlayer = currentTeam.squad.starting_xi.find(
                          (p: Player) => p.id === currentTeam.squad.captain.id
                        )
                        const viceCaptainPlayer = currentTeam.squad.starting_xi.find(
                          (p: Player) => p.id === currentTeam.squad.vice_captain.id
                        )
                        
                        return (
                          <>
                            <div className="flex items-center gap-2 text-sm mb-2">
                              <span className="text-gray-400">Captain:</span>
                              <span className="font-semibold text-[#00ff87]">{currentTeam.squad.captain.name}</span>
                              <span className="text-[#00ff87] font-mono">
                                ({(currentTeam.squad.captain.predicted ?? 0).toFixed(1)} × 2)
                              </span>
                              {captainPlayer?.opponent && (
                                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                                  (captainPlayer.difficulty ?? 3) <= 2 ? 'bg-green-500/20 text-green-400' :
                                  (captainPlayer.difficulty ?? 3) <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
                                  'bg-red-500/20 text-red-400'
                                }`}>
                                  {captainPlayer.is_home ? 'vs' : '@'} {captainPlayer.opponent}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 text-sm">
                              <span className="text-gray-400">Vice-Captain:</span>
                              <span className="font-semibold text-purple-400">{currentTeam.squad.vice_captain.name}</span>
                              <span className="text-purple-400 font-mono">
                                ({(currentTeam.squad.vice_captain.predicted ?? 0).toFixed(1)})
                              </span>
                              {viceCaptainPlayer?.opponent && (
                                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                                  (viceCaptainPlayer.difficulty ?? 3) <= 2 ? 'bg-green-500/20 text-green-400' :
                                  (viceCaptainPlayer.difficulty ?? 3) <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
                                  'bg-red-500/20 text-red-400'
                                }`}>
                                  {viceCaptainPlayer.is_home ? 'vs' : '@'} {viceCaptainPlayer.opponent}
                                </span>
                              )}
                            </div>
                          </>
                        )
                      })()}
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
}

export default SelectedTeamsTab

