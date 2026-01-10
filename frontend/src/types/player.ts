/**
 * Player-related type definitions
 */

export interface Player {
  id: number
  name: string
  full_name?: string
  team: string
  position: string
  position_id?: number
  price: number
  predicted?: number
  predicted_points?: number
  form?: number
  total_points?: number
  ownership?: number
  is_captain?: boolean
  is_vice_captain?: boolean
  rotation_risk?: string
  european_comp?: string
  opponent?: string
  difficulty?: number
  is_home?: boolean
  reason?: string
}

