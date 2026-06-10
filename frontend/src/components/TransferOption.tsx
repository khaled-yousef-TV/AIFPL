import React from 'react'
import type { TransferSuggestion } from '../types'

interface TransferOptionProps {
  suggestion: TransferSuggestion
  optionIndex: number
  getPositionClass: (position: string) => string
}

const TransferOption: React.FC<TransferOptionProps> = ({
  suggestion,
  optionIndex,
  getPositionClass,
}) => {
  return (
    <div className="p-3 bg-green-500/5 rounded-lg border border-green-500/20">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-green-400">#{optionIndex + 1}</span>
          <span className="text-sm font-medium text-content">Transfer In</span>
        </div>
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
          suggestion.points_gain > 0 ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        }`}>
          {suggestion.points_gain > 0 ? '+' : ''}{suggestion.points_gain} pts
        </span>
      </div>
      
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span className="font-medium text-sm">{suggestion.in.name}</span>
        {suggestion.in.european_comp && (
          <span className="px-1 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-400">
            {suggestion.in.european_comp}
          </span>
        )}
      </div>
      <div className="text-xs text-content-muted">{suggestion.in.team} • £{suggestion.in.price}m</div>
      <div className="text-xs text-content-subtle mt-1">
        vs {suggestion.in.fixture} (FDR {suggestion.in.fixture_difficulty}) • Form: {suggestion.in.form}
      </div>
      
      {/* Form Upgrade */}
      {suggestion.out.form && suggestion.in.form && parseFloat(suggestion.in.form) > parseFloat(suggestion.out.form) && (
        <div className="mt-2 flex items-center gap-1 text-xs">
          <span className="text-yellow-400">💡</span>
          <span className="text-primary font-medium">
            Form upgrade: {suggestion.out.form} → {suggestion.in.form}
          </span>
        </div>
      )}
      
      {/* Additional reasons */}
      {suggestion.all_reasons.length > 0 && (
        <div className="mt-2 text-xs text-content-muted">
          {suggestion.all_reasons[0] && (
            <div className="mb-1">
              {suggestion.all_reasons[0].includes('Also:') ? suggestion.all_reasons[0] : `Also: ${suggestion.all_reasons[0]}`}
            </div>
          )}
          {suggestion.all_reasons.slice(1).map((reason: string, idx: number) => (
            <div key={idx} className="text-content-subtle">• {reason}</div>
          ))}
        </div>
      )}
      
      {/* Why square - prettied up */}
      {suggestion.teammate_comparison?.why && (
        <div className="mt-3 pt-3 border-t border-border">
          <div className="p-3 bg-gradient-to-br from-surface/60 to-bg rounded-lg border border-primary/20">
            <div className="flex items-start gap-2 mb-2">
              <span className="text-primary text-sm">💡</span>
              <div className="flex-1">
                <div className="text-xs font-semibold text-content-muted mb-1">
                  Why {suggestion.in.name} over other {suggestion.teammate_comparison.team} {suggestion.teammate_comparison.position} options?
                </div>
                <div className="text-xs text-content-muted leading-relaxed">
                  {suggestion.teammate_comparison.why}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Cost and FDR */}
      <div className="flex gap-4 mt-2 text-xs text-content-muted pt-2 border-t border-border/50">
        <span>Cost: <span className={suggestion.cost > 0 ? 'text-red-400' : 'text-green-400'}>{suggestion.cost > 0 ? '+' : ''}£{suggestion.cost}m</span></span>
        <span>5GW Avg FDR: <span className="text-content-muted">{suggestion.out.avg_fixture_5gw}</span> → <span className="text-primary">{suggestion.in.avg_fixture_5gw}</span></span>
      </div>
    </div>
  )
}

export default TransferOption

