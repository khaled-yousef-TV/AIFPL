/**
 * FPL API-related type definitions
 */

export interface GameWeekInfo {
  current?: { id: number; name: string }
  next?: { id: number; name: string; deadline: string }
}


