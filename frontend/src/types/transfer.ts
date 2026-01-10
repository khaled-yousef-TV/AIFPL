/**
 * Transfer-related type definitions
 */

export interface TransferSuggestion {
  out: any
  in: any
  cost: number
  points_gain: number
  priority_score: number
  reason: string
  all_reasons: string[]
  teammate_comparison?: {
    team?: string
    position?: string
    why?: string
    chosen?: any
    alternatives?: any[]
  }
  type?: 'hold' | 'transfer'
  why?: string[]
  best_net_gain?: number | null
  hit_cost?: number
  best_alternative?: any
}

