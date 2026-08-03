/**
 * Top-level nav model: 4 primary views + a Scenarios overflow.
 *
 * The URL hash is the source of truth. hashToRoute() maps a raw hash to a
 * (top view, optional scenario run type) pair, keeping the run-type views
 * (my_team / wildcard / free_hit / triple_captain / differentials / squad)
 * reachable but grouped under Scenarios instead of the flat 8-item nav we
 * had before.
 */

import type { HermesRunType } from './api/hermes'

export type TopView = 'squad' | 'this_week' | 'season' | 'hermes' | 'scenario'

export interface PrimaryTab {
  id: TopView
  label: string
  shortLabel: string
  /** hash used in URL when this tab is active */
  hash: string
  description: string
}

export const PRIMARY_TABS: PrimaryTab[] = [
  { id: 'squad',     label: 'Squad',     shortLabel: 'Squad',   hash: '',           description: 'The persistent Hermes-driven benchmark squad' },
  { id: 'this_week', label: 'This week', shortLabel: 'This wk', hash: 'this-week',  description: 'Agents, news, odds, captaincy — the evidence' },
  { id: 'season',    label: 'Season',    shortLabel: 'Season',  hash: 'season',     description: 'Ledger vs template, season plan, chip roadmap' },
  { id: 'hermes',    label: 'Hermes',    shortLabel: 'Hermes',  hash: 'hermes',     description: 'Trust weights, hit-rates, lessons — is it learning?' },
]

/**
 * Scenario run types (accessible via the Scenarios menu). These render the
 * existing briefing layout with their run-type-specific content.
 */
export interface ScenarioItem {
  runType: HermesRunType
  label: string
  hash: string
  description: string
}

export const SCENARIO_ITEMS: ScenarioItem[] = [
  { runType: 'my_team',        label: 'My Team',        hash: 'my-team',        description: 'Personalized advice for your imported FPL team' },
  { runType: 'squad',          label: 'Best Squad',     hash: 'best-squad',     description: 'What a full rebuild would look like' },
  { runType: 'wildcard',       label: 'Wildcard',       hash: 'wildcard',       description: 'Full rebuild plan for the wildcard chip' },
  { runType: 'free_hit',       label: 'Free Hit',       hash: 'free-hit',       description: 'One-week-only optimal squad' },
  { runType: 'triple_captain', label: 'Triple Captain', hash: 'triple-captain', description: 'Highest-ceiling captaincy for TC' },
  { runType: 'differentials',  label: 'Differentials',  hash: 'differentials',  description: 'Low-ownership picks with strong signals' },
]

export interface Route {
  top: TopView
  runType?: HermesRunType
}

export const DEFAULT_ROUTE: Route = { top: 'squad' }

export function hashToRoute(hash: string): Route {
  const clean = hash.replace(/^#/, '')
  const primary = PRIMARY_TABS.find((t) => t.hash === clean)
  if (primary) return { top: primary.id }
  const scenario = SCENARIO_ITEMS.find((s) => s.hash === clean)
  if (scenario) return { top: 'scenario', runType: scenario.runType }
  return DEFAULT_ROUTE
}

export function routeToHash(route: Route): string {
  if (route.top === 'scenario' && route.runType) {
    const s = SCENARIO_ITEMS.find((s) => s.runType === route.runType)
    return s ? s.hash : ''
  }
  const primary = PRIMARY_TABS.find((t) => t.id === route.top)
  return primary?.hash ?? ''
}
