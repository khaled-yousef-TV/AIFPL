// Pitch rendering helpers extracted from App.tsx.
// These are plain render functions (not components) so existing call sites
// and prop-passing in App.tsx stay identical.
import { X } from 'lucide-react'

import type { SquadPlayer } from '../types'
import { getPositionClass, parseFormation } from '../utils/squad'

// Render a single player pill (uniform size)
export const renderPlayerPill = (player: any | null, isEmpty: boolean = false, showRemoveButton: boolean = false, onRemove?: (id: number) => void) => {
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
            onRemove?.(player.id)
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
export const renderPlayerPillWithTransfer = (player: any | null, isEmpty: boolean, isTransferOut: boolean, isTransferIn: boolean) => {
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
export const renderBeforeAfterPitch = (
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
export const renderPitchFormation = (startingXi: any[], formation: string, showEmptySlots: boolean = false) => {
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
// (state and handlers that App.tsx closed over are passed in as parameters)
export const renderTransfersPitch = (
  mySquad: SquadPlayer[],
  playerSlotPositions: Map<number, { position: string; slotIndex: number }>,
  setPlayerSlotPositions: (value: any) => void,
  isPositionFull: (position: string) => boolean,
  setSearchPosition: (value: any) => void,
  searchQuery: string,
  searchPlayers: (query: string, position?: string) => void,
  onRemove: (id: number) => void
) => {
  // Use standard FPL formation (3-5-2) to show all slots - 2 GKs allowed
  const formation = { def: 5, mid: 5, fwd: 3, gk: 2 }

  // Group current squad by position and maintain slot positions
  // Create slot-based structure to preserve positions when removing players
  const getSlotsForPosition = (position: string, maxSlots: number) => {
    const players = mySquad.filter(p => p.position === position)
    const slots: (SquadPlayer | null)[] = new Array(maxSlots).fill(null)
    const pendingUpdates = new Map<number, { position: string; slotIndex: number }>()

    // Fill slots based on stored slot positions, preserving positions when players are removed
    players.forEach((player) => {
      const slotInfo = playerSlotPositions.get(player.id)
      if (slotInfo && slotInfo.slotIndex < maxSlots && slots[slotInfo.slotIndex] === null) {
        // Use stored slot position if available
        slots[slotInfo.slotIndex] = player
      } else {
        // If no stored position or slot is taken, find first available slot
        for (let i = 0; i < maxSlots; i++) {
          if (slots[i] === null) {
            slots[i] = player
            // Track pending update (will be applied after render)
            if (!slotInfo || slotInfo.slotIndex !== i) {
              pendingUpdates.set(player.id, { position, slotIndex: i })
            }
            break
          }
        }
      }
    })

    // Apply pending updates after render completes
    if (pendingUpdates.size > 0) {
      setTimeout(() => {
        const newMap = new Map(playerSlotPositions)
        pendingUpdates.forEach((value, key) => {
          newMap.set(key, value)
        })
        setPlayerSlotPositions(newMap)
      }, 0)
    }

    return slots
  }

  const byPosition = {
    GK: getSlotsForPosition('GK', formation.gk),
    DEF: getSlotsForPosition('DEF', formation.def),
    MID: getSlotsForPosition('MID', formation.mid),
    FWD: getSlotsForPosition('FWD', formation.fwd),
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
          {byPosition.GK.map((slot, idx) => {
            const isEmpty = slot === null
            const isFull = isPositionFull('GK')
            return (
              <div
                key={`gk-${idx}`}
                onClick={() => isEmpty && !isFull && handleSlotClick('GK')}
                className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                title={isEmpty && !isFull ? 'Click to search for GK players' : isEmpty && isFull ? 'GK position full' : ''}
              >
                {renderPlayerPill(slot, isEmpty, true, onRemove)}
              </div>
            )
          })}
        </div>

        {/* Defenders */}
        <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
          {byPosition.DEF.map((slot, idx) => {
            const isEmpty = slot === null
            const isFull = isPositionFull('DEF')
            return (
              <div
                key={`def-${idx}`}
                onClick={() => isEmpty && !isFull && handleSlotClick('DEF')}
                className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                title={isEmpty && !isFull ? 'Click to search for DEF players' : isEmpty && isFull ? 'DEF position full' : ''}
              >
                {renderPlayerPill(slot, isEmpty, true, onRemove)}
              </div>
            )
          })}
        </div>

        {/* Midfielders */}
        <div className="flex justify-center items-center gap-2 sm:gap-3 mb-3 sm:mb-4 flex-wrap">
          {byPosition.MID.map((slot, idx) => {
            const isEmpty = slot === null
            const isFull = isPositionFull('MID')
            return (
              <div
                key={`mid-${idx}`}
                onClick={() => isEmpty && !isFull && handleSlotClick('MID')}
                className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                title={isEmpty && !isFull ? 'Click to search for MID players' : isEmpty && isFull ? 'MID position full' : ''}
              >
                {renderPlayerPill(slot, isEmpty, true, onRemove)}
              </div>
            )
          })}
        </div>

        {/* Forwards (BOTTOM) */}
        <div className="flex justify-center items-center gap-2 sm:gap-3 flex-wrap">
          {byPosition.FWD.map((slot, idx) => {
            const isEmpty = slot === null
            const isFull = isPositionFull('FWD')
            return (
              <div
                key={`fwd-${idx}`}
                onClick={() => isEmpty && !isFull && handleSlotClick('FWD')}
                className={isEmpty && !isFull ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}
                title={isEmpty && !isFull ? 'Click to search for FWD players' : isEmpty && isFull ? 'FWD position full' : ''}
              >
                {renderPlayerPill(slot, isEmpty, true, onRemove)}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
