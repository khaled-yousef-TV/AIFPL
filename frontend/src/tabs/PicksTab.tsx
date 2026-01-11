import React from 'react'
import { Star } from 'lucide-react'
import type { Player } from '../types'

interface PicksTabProps {
  topPicks: Record<string, Player[]>
}

const PicksTab: React.FC<PicksTabProps> = ({ topPicks }) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
      {Object.entries(topPicks).map(([position, players]) => (
        <div key={position} className="card">
          <div className="card-header capitalize">
            <Star className="w-5 h-5 text-yellow-400" />
            Top {position}
          </div>
          <div className="space-y-2">
            {players.map((player, i) => (
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
                    <div className="text-[#00ff87] font-mono font-semibold">
                      {player.predicted_points?.toFixed(1) ?? '0.0'}
                    </div>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      (player.difficulty ?? 3) <= 2 ? 'bg-green-500/20 text-green-400' :
                      (player.difficulty ?? 3) <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
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
  )
}

export default PicksTab

