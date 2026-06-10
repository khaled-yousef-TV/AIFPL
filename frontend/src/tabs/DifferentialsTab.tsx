import React from 'react'
import { Flame } from 'lucide-react'
import type { Player } from '../types'

interface DifferentialsTabProps {
  differentials: Player[]
  getPositionClass: (position: string) => string
}

const DifferentialsTab: React.FC<DifferentialsTabProps> = ({ differentials, getPositionClass }) => {
  return (
    <div className="card">
      <div className="card-header">
        <Flame className="w-5 h-5 text-orange-400" />
        Differentials (Under 10% Owned)
      </div>
      <p className="text-content-muted text-sm mb-4">
        Low-ownership players with high predicted points - great for climbing ranks!
      </p>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="text-left text-content-muted text-sm border-b border-border">
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
            {differentials.map((player, i) => (
              <tr key={player.id} className="border-b border-border/50 hover:bg-surface-2 transition-colors">
                <td className="py-3 text-content-subtle font-mono">{i + 1}</td>
                <td className="py-3">
                  <span className="font-medium">{player.name}</span>
                  <span className="text-content-subtle text-xs ml-1">({player.team})</span>
                </td>
                <td className="py-3">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                    (player.difficulty ?? 3) <= 2 ? 'bg-green-500/20 text-green-400' :
                    (player.difficulty ?? 3) <= 3 ? 'bg-yellow-500/20 text-yellow-400' :
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
                <td className="py-3 text-right font-mono text-primary font-semibold">
                  {player.predicted_points?.toFixed(1) ?? '0.0'}
                </td>
                <td className="py-3 text-xs text-content-muted max-w-[150px]">{player.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default DifferentialsTab

