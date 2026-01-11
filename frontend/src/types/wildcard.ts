/**
 * Wildcard Trajectory type definitions
 */

export interface GameweekPrediction {
  predicted: number
  hybrid: number
  fdr_adjusted: number
  opponent: string
  fdr: number
  is_home: boolean
}

export interface TrajectoryPlayer {
  id: number
  name: string
  team: string
  team_id: number
  position: string
  position_id: number
  price: number
  form: number
  total_points: number
  ownership: number
  predicted_points: number
  avg_fdr: number
  fixture_swing: number
  gameweek_predictions: Record<number, GameweekPrediction>
}

export interface GameweekBreakdown {
  gameweek: number
  formation: string
  predicted_points: number
  starting_xi: Array<{
    id: number
    name: string
    team: string
    position: string
    predicted: number
    opponent: string
    fdr: number
    is_home: boolean
  }>
}

export interface FixtureBlock {
  team: string
  players: string[]
  green_runs: Array<Array<{
    gw: number
    fdr: number
    opponent: string
    is_home: boolean
  }>>
  avg_fdr: number
}

export interface WildcardTrajectory {
  squad: TrajectoryPlayer[]
  starting_xi: TrajectoryPlayer[]
  bench: TrajectoryPlayer[]
  captain: TrajectoryPlayer
  vice_captain: TrajectoryPlayer
  formation: string
  gameweek_predictions: Record<number, GameweekBreakdown>
  total_predicted_points: number
  avg_weekly_points: number
  total_cost: number
  remaining_budget: number
  horizon: number
  fixture_blocks: FixtureBlock[]
  rationale: string
}

