import React, { RefObject, useRef, useEffect } from 'react'
import { 
  ArrowRightLeft, Search, Plus, X, RefreshCw, TrendingUp, 
  Target, Users, Star, ChevronUp, ChevronDown
} from 'lucide-react'
import type { SquadPlayer, TransferSuggestion, SavedFplTeam, Player } from '../types'
import TransferOption from '../components/TransferOption'

// Position limits for FPL squad
const POSITION_LIMITS: Record<string, number> = {
  'GK': 2,
  'DEF': 5,
  'MID': 5,
  'FWD': 3,
}


interface GroupedSuggestions {
  holdSuggestions: TransferSuggestion[]
  sortedGroups: {
    outPlayer: SquadPlayer
    suggestions: TransferSuggestion[]
  }[]
}

interface WildcardPlan {
  before_total_points?: number
  after_total_points?: number
  total_points_gain?: number
  total_cost?: number
  transfers_out?: Player[]
  transfers_in?: Player[]
  kept_players?: Player[]
  individual_breakdowns?: any[]
  combined_rationale?: string
  before_formation?: string
  resulting_squad?: {
    squad: Player[]
    formation: string | Record<string, number>
  }
}

interface TransfersTabProps {
  // Squad state
  mySquad: SquadPlayer[]
  setMySquad: (squad: SquadPlayer[]) => void
  bank: number
  setBank: (bank: number) => void
  bankInput: string
  setBankInput: (val: string) => void
  freeTransfers: number
  setFreeTransfers: (ft: number) => void
  
  // Search state
  searchQuery: string
  setSearchQuery: (query: string) => void
  searchPosition: string
  setSearchPosition: (pos: string) => void
  searchResults: SquadPlayer[]
  searchPlayers: (query: string, position: string) => void
  
  // Squad operations
  addToSquad: (player: SquadPlayer) => void
  removeFromSquad: (id: number) => void
  updateSquadPrice: (id: number, price: number) => void
  isPositionFull: (position: string) => boolean
  getPositionCount: (position: string) => number
  
  // Transfer suggestions
  transferSuggestions: TransferSuggestion[]
  setTransferSuggestions: (suggestions: TransferSuggestion[]) => void
  groupedTransferSuggestions: GroupedSuggestions | null
  transferLoading: boolean
  getTransferSuggestions: () => Promise<void>
  
  // Wildcard
  wildcardPlan: WildcardPlan | null
  setWildcardPlan: (plan: WildcardPlan | null) => void
  wildcardLoading: boolean
  setWildcardLoading: (loading: boolean) => void
  
  // Expand/collapse groups
  expandedGroups: Set<string>
  setExpandedGroups: (groups: Set<string>) => void
  
  // Squad analysis
  squadAnalysis: any[]
  
  // Error handling
  error: string | null
  setError: (error: string | null) => void
  
  // FPL Import
  savedFplTeams: SavedFplTeam[]
  selectedSavedFplTeamId: number | string
  setSelectedSavedFplTeamId: (id: number | string) => void
  fplTeamId: string
  setFplTeamId: (id: string) => void
  importingFplTeam: boolean
  importFplTeam: () => void
  importFromSavedFplTeam: (teamId: number) => void
  
  // Rendering helpers
  getPositionClass: (position: string) => string
  renderTransfersPitch: () => React.ReactNode
  renderBeforeAfterPitch: (
    beforeSquad: Player[],
    afterSquad: Player[],
    beforeFormation: string,
    afterFormation: string,
    transfersOut: Player[],
    transfersIn: Player[]
  ) => React.ReactNode
  parseFormation: (formation: string) => { DEF: number; MID: number; FWD: number }
  
  // API
  API_BASE: string
  
  // Refs
  resultsSectionRef: RefObject<HTMLDivElement>
  squadSectionRef: RefObject<HTMLDivElement>
}

const TransfersTab: React.FC<TransfersTabProps> = ({
  mySquad,
  setMySquad,
  bank,
  setBank,
  bankInput,
  setBankInput,
  freeTransfers,
  setFreeTransfers,
  searchQuery,
  setSearchQuery,
  searchPosition,
  setSearchPosition,
  searchResults,
  searchPlayers,
  addToSquad,
  removeFromSquad,
  updateSquadPrice,
  isPositionFull,
  getPositionCount,
  transferSuggestions,
  setTransferSuggestions,
  groupedTransferSuggestions,
  transferLoading,
  getTransferSuggestions,
  wildcardPlan,
  setWildcardPlan,
  wildcardLoading,
  setWildcardLoading,
  expandedGroups,
  setExpandedGroups,
  squadAnalysis,
  error,
  setError,
  savedFplTeams,
  selectedSavedFplTeamId,
  setSelectedSavedFplTeamId,
  fplTeamId,
  setFplTeamId,
  importingFplTeam,
  importFplTeam,
  importFromSavedFplTeam,
  getPositionClass,
  renderTransfersPitch,
  renderBeforeAfterPitch,
  parseFormation,
  API_BASE,
  resultsSectionRef,
  squadSectionRef,
}) => {
  // Ref for search results section to enable auto-scroll
  const searchResultsRef = useRef<HTMLDivElement>(null)
  
  // Track previous searchPosition to detect changes
  const prevSearchPositionRef = useRef<string>(searchPosition)
  
  // Auto-scroll to search section when position filter changes
  useEffect(() => {
    if (searchPosition !== prevSearchPositionRef.current) {
      setTimeout(() => {
        if (searchResultsRef.current) {
          searchResultsRef.current.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'start'
          })
        }
      }, 50)
    }
    prevSearchPositionRef.current = searchPosition
  }, [searchPosition])

  const handleGenerateSuggestions = async () => {
    if (mySquad.length < 15) {
      setError('Please add all 15 players to your squad')
      return
    }
    setError(null)
    
    if (freeTransfers <= 3) {
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
        
        // Scroll to results
        setTimeout(() => {
          if (resultsSectionRef.current) {
            const element = resultsSectionRef.current
            const elementPosition = element.getBoundingClientRect().top + window.pageYOffset
            const offsetPosition = elementPosition + 100
            window.scrollTo({
              top: offsetPosition,
              behavior: 'smooth'
            })
          }
        }, 100)
      } catch (err) {
        console.error('Wildcard error:', err)
        setError(err instanceof Error ? err.message : 'Failed to generate wildcard plan')
        setWildcardPlan(null)
      } finally {
        setWildcardLoading(false)
      }
    }
  }

  return (
    <>
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
          
          {/* FPL Team Import */}
          <div className="mt-4 p-3 sm:p-4 bg-[#0f0f1a] rounded-lg border border-[#2a2a4a]">
            <div className="space-y-3">
              {/* Previously imported teams */}
              {savedFplTeams.length > 0 && (
                <div>
                  <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                    <span className="text-xs text-gray-400 whitespace-nowrap">Your teams</span>
                    <select
                      value={selectedSavedFplTeamId}
                      onChange={(e) => {
                        const teamId = e.target.value ? parseInt(e.target.value) : ''
                        setSelectedSavedFplTeamId(teamId)
                        if (teamId && typeof teamId === 'number') {
                          importFromSavedFplTeam(teamId)
                        }
                      }}
                      disabled={importingFplTeam}
                      className="flex-1 px-3 py-1.5 sm:py-1 bg-[#0b0b14] border border-[#2a2a4a] rounded text-sm focus:border-[#00ff87] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <option value="">— Select a team to refresh —</option>
                      {savedFplTeams.map((team) => (
                        <option key={team.teamId} value={team.teamId}>
                          {team.teamName} (ID: {team.teamId})
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="text-[10px] text-gray-500 mt-1.5">
                    Select a previously imported team to fetch the latest squad from FPL
                  </div>
                </div>
              )}
              
              {/* Import new team */}
              <div className={savedFplTeams.length > 0 ? "pt-3 border-t border-[#2a2a4a]" : ""}>
                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                  <span className="text-xs text-gray-400 whitespace-nowrap">Import team</span>
                  <input
                    type="number"
                    value={fplTeamId}
                    onChange={(e) => setFplTeamId(e.target.value)}
                    disabled={importingFplTeam}
                    className="flex-1 px-3 py-1.5 sm:py-1 bg-[#0b0b14] border border-[#2a2a4a] rounded text-sm focus:border-[#00ff87] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
                    placeholder="Enter FPL Team ID"
                  />
                  <button
                    onClick={importFplTeam}
                    disabled={!fplTeamId.trim() || importingFplTeam}
                    className="btn btn-secondary text-xs sm:text-sm flex-1 sm:flex-none disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
                  >
                    {importingFplTeam ? (
                      <>
                        <RefreshCw className="w-3 h-3 animate-spin" />
                        <span className="hidden sm:inline">Importing...</span>
                      </>
                    ) : (
                      'Import'
                    )}
                  </button>
                </div>
                <div className="text-[10px] text-gray-500 mt-1.5">
                  Enter your FPL Team ID to import your squad. Find it in your FPL profile URL (e.g., fantasy.premierleague.com/entry/<strong>123456</strong>/event/1).
                </div>
              </div>
            </div>
            <div className="text-[10px] sm:text-[11px] text-gray-500 mt-2">
              Your squad is auto-saved locally. Importing will fetch the latest data from FPL.
            </div>
          </div>

          {/* Squad Input */}
          <div className="space-y-6">
            {/* Search & Add */}
            <div ref={searchResultsRef} className="scroll-mt-4">
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
            
            {/* Bank & Free Transfers */}
            <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 mt-6 pt-4 border-t border-[#2a2a4a]">
              <div className="flex items-center gap-2">
                <label className="text-sm text-gray-400 whitespace-nowrap">Bank (£m)</label>
                <input
                  type="number"
                  step="0.1"
                  value={bankInput}
                  onChange={(e) => {
                    const val = e.target.value
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
              <div ref={squadSectionRef} className="scroll-mt-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-medium">Your Squad ({mySquad.length}/15)</h3>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={handleGenerateSuggestions}
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
          <div ref={resultsSectionRef} className="card">
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
                      {firstOption && (
                        <TransferOption 
                          suggestion={firstOption} 
                          optionIndex={0} 
                          getPositionClass={getPositionClass}
                        />
                      )}
                      
                      {isExpanded && otherOptions.map((suggestion, optionIndex) => (
                        <TransferOption 
                          key={`option-${optionIndex + 1}`}
                          suggestion={suggestion} 
                          optionIndex={optionIndex + 1} 
                          getPositionClass={getPositionClass}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
        
        {/* Wildcard Results */}
        {freeTransfers >= 4 && wildcardPlan && (
          <div ref={resultsSectionRef} className="space-y-6">
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
                  <div className={`text-xl font-bold ${wildcardPlan.total_cost && wildcardPlan.total_cost < 0 ? 'text-green-400' : wildcardPlan.total_cost && wildcardPlan.total_cost > 0 ? 'text-red-400' : 'text-gray-300'}`}>
                    {wildcardPlan.total_cost && wildcardPlan.total_cost > 0 ? '+' : ''}£{wildcardPlan.total_cost?.toFixed(1) || '0.0'}m
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
            
            {/* Before/After Squad Comparison - Pitch Formation */}
            {wildcardPlan.resulting_squad?.squad && (
              <div className="card">
                <div className="card-header">
                  <Users className="w-5 h-5 text-[#00ff87]" />
                  Squad Comparison
                </div>
                {(() => {
                  const byPosition = {
                    GK: mySquad.filter((p) => p.position === 'GK'),
                    DEF: mySquad.filter((p) => p.position === 'DEF'),
                    MID: mySquad.filter((p) => p.position === 'MID'),
                    FWD: mySquad.filter((p) => p.position === 'FWD'),
                  }
                  
                  const beforeFormation = wildcardPlan.before_formation || 
                    (() => {
                      const defCount = Math.min(byPosition.DEF.length, 5)
                      const midCount = Math.min(byPosition.MID.length, 5)
                      const fwdCount = Math.min(byPosition.FWD.length, 3)
                      if (defCount + midCount + fwdCount + 1 === 11) {
                        return `${defCount}-${midCount}-${fwdCount}`
                      }
                      return '3-5-2'
                    })()
                  
                  const beforeFullSquad = mySquad
                  const afterFullSquad = wildcardPlan.resulting_squad!.squad
                  
                  const afterFormationRaw = wildcardPlan.resulting_squad!.formation || '3-5-2'
                  const afterFormation = typeof afterFormationRaw === 'string' 
                    ? afterFormationRaw 
                    : `${(afterFormationRaw as Record<string, number>).DEF || 3}-${(afterFormationRaw as Record<string, number>).MID || 5}-${(afterFormationRaw as Record<string, number>).FWD || 2}`
                  
                  return renderBeforeAfterPitch(
                    beforeFullSquad as Player[],
                    afterFullSquad,
                    beforeFormation,
                    afterFormation,
                    wildcardPlan.transfers_out || [],
                    wildcardPlan.transfers_in || []
                  )
                })()}
              </div>
            )}
            
            {/* Kept Players and Transfer Breakdown */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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
      
      {/* Squad Analysis - Only shown in Quick Transfers (1-3 transfers) */}
      {freeTransfers <= 3 && squadAnalysis.length > 0 && (
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
    </>
  )
}

export default TransfersTab

