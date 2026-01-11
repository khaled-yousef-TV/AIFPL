import type { Player } from '../types'

interface PlayerCardProps {
  player: Player
  index?: number
  showReason?: boolean
  showFixture?: boolean
  compact?: boolean
}

export function PlayerCard({ 
  player, 
  index,
  showReason = false,
  showFixture = true,
  compact = false 
}: PlayerCardProps) {
  const difficultyClass = 
    (player.difficulty ?? 3) <= 2 ? 'bg-green-500/20 text-green-400' :
    (player.difficulty ?? 3) <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
    'bg-red-500/20 text-red-400'

  if (compact) {
    return (
      <div className="flex items-center justify-between py-2">
        <div className="flex items-center gap-2">
          {index !== undefined && (
            <span className="text-gray-500 font-mono w-4 text-sm">{index + 1}</span>
          )}
          <span className="font-medium">{player.name}</span>
          <span className="text-gray-500 text-sm">{player.team}</span>
        </div>
        <span className="text-[#00ff87] font-mono">
          {(player.predicted_points ?? player.predicted ?? 0).toFixed(1)}
        </span>
      </div>
    )
  }

  return (
    <div className="p-3 bg-[#0f0f1a] rounded-lg">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {index !== undefined && (
            <span className="text-gray-500 font-mono w-4">{index + 1}</span>
          )}
          <div>
            <div className="font-medium">{player.name}</div>
            <div className="text-sm text-gray-400">
              {player.team} • £{player.price}m
            </div>
          </div>
        </div>
        <div className="text-right">
          <div className="text-[#00ff87] font-mono font-semibold">
            {(player.predicted_points ?? player.predicted ?? 0).toFixed(1)}
          </div>
          {showFixture && player.opponent && (
            <span className={`text-xs px-1.5 py-0.5 rounded ${difficultyClass}`}>
              {player.is_home ? 'vs' : '@'} {player.opponent}
            </span>
          )}
        </div>
      </div>
      {showReason && player.reason && (
        <div className="text-xs text-gray-500 mt-2 pl-7">{player.reason}</div>
      )}
    </div>
  )
}

