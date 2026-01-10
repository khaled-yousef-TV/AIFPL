/**
 * Squad-related type definitions
 */

import type { Player } from './player'

export interface SuggestedSquad {
  gameweek: number
  formation: string
  starting_xi: Player[]
  bench: Player[]
  captain: { id: number; name: string; predicted: number }
  vice_captain: { id: number; name: string; predicted: number }
  total_cost: number
  remaining_budget: number
  predicted_points: number
}

export interface SquadPlayer {
  id: number
  name: string
  position: string
  // IMPORTANT: For "Quick Transfers" this should be the user's SELLING price.
  // Search results provide current price, which may differ from selling price.
  price: number
  team?: string
  rotation_risk?: string
  european_comp?: string
}

export interface SelectedTeam {
  gameweek: number
  squad: SuggestedSquad
  saved_at: string
}

