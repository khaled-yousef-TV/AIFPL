/**
 * This Week — the single Hermes report page.
 *
 * Verdict first, evidence below: headline decision, pitch view, captaincy,
 * chips, the adjustment ledger ("what Hermes changed and why"), agent
 * evidence, and the full narrative as a collapsible appendix.
 *
 * The old per-run-type tabs are folded in as views: the briefing is the
 * default, and deep dives (wildcard rebuild, free hit, ...) render into the
 * same layout.
 */
import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Moon,
  Sparkles,
} from 'lucide-react'
import type { ActiveHermesRun, AgentReport, HermesRun, HermesRunType, HermesStatus } from '../api/hermes'
import { fetchLlmProviders, setLlmProvider, type LlmProvidersResponse } from '../api/hermes'
import { HERMES_RUN_TYPES, type HermesRunTypeInfo } from '../hooks/useHermes'
import { apiRequest } from '../api/client'
import { formatDuration, formatRelative } from '../utils/time'

// ==================== LLM provider switcher ====================

const ModelSwitcher: React.FC = () => {
  const [info, setInfo] = useState<LlmProvidersResponse | null>(null)
  const [switching, setSwitching] = useState(false)

  useEffect(() => {
    fetchLlmProviders().then(setInfo).catch(() => setInfo(null))
  }, [])

  // Hide entirely when fewer than two providers are configured
  const configured = info?.providers.filter((p) => p.configured) ?? []
  if (!info || configured.length < 2) return null

  const change = async (provider: string) => {
    setSwitching(true)
    try {
      const res = await setLlmProvider(provider)
      setInfo((s) => (s ? { ...s, active: res.active, active_model: res.active_model } : s))
    } catch {
      // leave the previous selection; the select re-renders from state
    } finally {
      setSwitching(false)
    }
  }

  return (
    <label className="flex items-center gap-1.5 text-xs text-content-subtle" title="Takes effect on the next run — no restart needed">
      Model:
      <select
        value={info.active ?? ''}
        disabled={switching}
        onChange={(e) => e.target.value && change(e.target.value)}
        className="bg-bg border border-border rounded-md px-2 py-1 text-xs text-content focus:outline-none focus:border-[#00ff87]/50"
      >
        {info.active === null && <option value="">custom (LLM_* env)</option>}
        {configured.map((p) => (
          <option key={p.id} value={p.id}>
            {p.id} · {p.model}
          </option>
        ))}
      </select>
    </label>
  )
}

// ==================== My Team setup (localStorage-backed) ====================

const TEAM_ID_KEY = 'fpl_team_id'
const TEAM_NAME_KEY = 'fpl_team_name'

export function getStoredTeam(): { id: number; name: string } | null {
  const raw = localStorage.getItem(TEAM_ID_KEY)
  const id = raw ? Number(raw) : NaN
  if (!Number.isInteger(id) || id <= 0) return null
  return { id, name: localStorage.getItem(TEAM_NAME_KEY) || `FPL Team ${id}` }
}

const TeamSetup: React.FC<{
  onSaved: (team: { id: number; name: string }, picksAvailable: boolean) => void
}> = ({ onSaved }) => {
  const [value, setValue] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const importTeam = async () => {
    const id = Number(value.trim())
    if (!Number.isInteger(id) || id <= 0) {
      setError('Enter a numeric FPL team ID')
      return
    }
    setImporting(true)
    setError(null)
    try {
      const result = await apiRequest<{ team_name?: string; picks_available?: boolean }>(
        `/api/fpl-teams/import/${id}`,
      )
      const team = { id, name: result.team_name || `FPL Team ${id}` }
      localStorage.setItem(TEAM_ID_KEY, String(id))
      localStorage.setItem(TEAM_NAME_KEY, team.name)
      onSaved(team, result.picks_available !== false)
    } catch (e: any) {
      setError(e?.message || 'Import failed — check the team ID')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="card">
      <h4 className="text-sm font-medium text-content mb-1">Connect your FPL team</h4>
      <p className="text-sm text-content-muted mb-4">
        Hermes analyzes your actual squad: transfers out/in, captaincy from your players, and
        chip timing for your situation. Find your team ID in the FPL site URL —{' '}
        <span className="text-content-subtle">fantasy.premierleague.com/entry/</span>
        <span className="text-[#00ff87]">1234567</span>
        <span className="text-content-subtle">/history</span>.
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !importing && importTeam()}
          placeholder="Team ID, e.g. 4843814"
          className="bg-bg border border-border rounded-lg px-3 py-2 text-sm text-content w-48 focus:outline-none focus:border-[#00ff87]/50"
        />
        <button onClick={importTeam} disabled={importing} className="btn btn-primary flex items-center gap-2">
          {importing && <Loader2 className="w-4 h-4 animate-spin" />}
          {importing ? 'Importing…' : 'Import team'}
        </button>
      </div>
      {error && (
        <p className="mt-3 text-sm text-red-400 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </p>
      )}
    </div>
  )
}

const AGENT_LABELS: Record<string, string> = {
  data: 'Data',
  mechanics: 'Game Mechanics',
  availability: 'Availability',
  form: 'Form',
  variability: 'Variability',
  betting: 'Betting Market',
  news: 'News & Sentiment',
}

// ==================== Narrative (markdown-lite) ====================

function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i} className="text-content font-semibold">{part.slice(2, -2)}</strong>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    ),
  )
}

function Narrative({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <div className="space-y-1.5 text-sm text-content-muted leading-relaxed">
      {lines.map((line, i) => {
        const t = line.trim()
        if (!t) return <div key={i} className="h-1" />
        if (t.startsWith('### ')) return <h4 key={i} className="text-content font-semibold mt-3">{renderInline(t.slice(4))}</h4>
        if (t.startsWith('## ')) return <h3 key={i} className="text-content font-semibold text-base mt-3">{renderInline(t.slice(3))}</h3>
        if (t.startsWith('# ')) return <h3 key={i} className="text-content font-semibold text-base mt-3">{renderInline(t.slice(2))}</h3>
        if (/^[-*]\s/.test(t)) return <div key={i} className="flex gap-2 pl-1"><span className="text-primary">•</span><span>{renderInline(t.slice(2))}</span></div>
        return <p key={i}>{renderInline(t)}</p>
      })}
    </div>
  )
}

function useElapsed(startIso: string | null | undefined, running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [running])
  if (!startIso) return 0
  const start = new Date(startIso).getTime()
  return Number.isFinite(start) ? Math.max(0, now - start) : 0
}

// ==================== Payload helpers ====================

interface Adjustment {
  player_id: number
  multiplier: number
  action: string
  reason: string
}

function getAdjustments(run: HermesRun | null): Adjustment[] {
  const raw = (run?.adjustments as any)?.adjustments
  if (!Array.isArray(raw)) return []
  return raw.filter((a: any) => a && typeof a.player_id === 'number' && typeof a.multiplier === 'number')
}

/** Player ids -> names/details, collected from every list in the result. */
function buildPlayerIndex(run: HermesRun | null): Map<number, any> {
  const index = new Map<number, any>()
  const result = run?.result
  if (!result) return index
  const lists = [
    result.squad?.starting_xi,
    result.squad?.bench,
    result.captain_ranking,
    result.differentials,
  ]
  // Agent payloads (data, availability, form, ...) carry id+name lists for
  // players outside the squad — the adjustment ledger needs those names too.
  if (run?.signals) {
    for (const report of Object.values(run.signals)) {
      for (const value of Object.values(report.payload || {})) {
        if (Array.isArray(value)) lists.push(value)
      }
    }
  }
  for (const list of lists) {
    if (!Array.isArray(list)) continue
    for (const p of list) {
      if (p?.id != null && p?.name && !index.has(p.id)) index.set(p.id, p)
    }
  }
  return index
}

/** One-sentence answer to "what should I do this week". */
function deriveVerdict(run: HermesRun): string {
  const r = run.result
  if (!r) return run.error ? 'Run failed — see details below.' : 'No structured result — read the full report below.'

  const parts: string[] = []
  const captain = r.squad?.captain?.name || r.captain_ranking?.[0]?.name
  if (captain) parts.push(`Captain ${captain}.`)

  if (run.run_type === 'triple_captain' && r.triple_captain) {
    return r.triple_captain.play_now && r.triple_captain.player_name
      ? `Play Triple Captain on ${r.triple_captain.player_name} this week.`
      : 'Hold your Triple Captain.'
  }
  if (run.run_type === 'differentials' && Array.isArray(r.differentials) && r.differentials.length > 0) {
    const names = r.differentials.slice(0, 3).map((d: any) => d.name).join(', ')
    return `Best differentials: ${names}.`
  }
  if (run.run_type === 'wildcard') parts.unshift('Wildcard rebuild:')
  if (run.run_type === 'free_hit') parts.unshift('Free Hit squad:')

  const transfers = r.transfer_priorities
  if (Array.isArray(transfers) && transfers.length > 0) {
    parts.push(`${transfers.length} transfer${transfers.length > 1 ? 's' : ''} suggested.`)
  } else if (run.run_type === 'briefing' || run.run_type === 'my_team') {
    parts.push('Hold your transfers.')
  }

  const chips = r.chip_advice
  if (chips) {
    const playNow = [
      chips.wildcard_now && 'Wildcard',
      chips.free_hit_now && 'Free Hit',
      chips.bench_boost_now && 'Bench Boost',
      r.triple_captain?.play_now && 'Triple Captain',
    ].filter(Boolean)
    parts.push(playNow.length > 0 ? `Play ${playNow.join(' + ')} now.` : 'No chips this week.')
  }

  return parts.join(' ') || 'Read the full report below.'
}

// ==================== Pitch ====================

const POSITION_ORDER = ['GK', 'DEF', 'MID', 'FWD']

function adjustmentFor(adjustments: Adjustment[], playerId: number): Adjustment | undefined {
  return adjustments.find((a) => a.player_id === playerId)
}

const PlayerDot: React.FC<{ player: any; adjustment?: Adjustment }> = ({ player, adjustment }) => {
  const boosted = adjustment && adjustment.multiplier > 1
  const faded = adjustment && adjustment.multiplier < 1
  const ring = player.is_captain
    ? 'ring-2 ring-magenta'
    : boosted
    ? 'ring-2 ring-green-400/70'
    : faded
    ? 'ring-2 ring-red-400/60'
    : 'ring-1 ring-[#2a2a4a]'
  const tooltip = [
    `${player.name} (${player.team}) — ${Number(player.predicted).toFixed(1)} pts, £${player.price}m`,
    player.opponent ? `${player.is_home ? 'vs' : '@'} ${player.opponent} (FDR ${player.difficulty})` : null,
    adjustment ? `Hermes ${adjustment.action} ×${adjustment.multiplier}: ${adjustment.reason}` : null,
    player.reason || null,
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <div className="flex flex-col items-center w-16 sm:w-[4.5rem]" title={tooltip}>
      <div
        className={`w-10 h-10 sm:w-11 sm:h-11 rounded-full flex items-center justify-center text-[10px] font-bold ${ring} ${
          player.is_captain ? 'bg-magenta/20 text-magenta' : 'bg-[#1a1a2e] text-content'
        }`}
      >
        {player.is_captain ? 'C' : player.team}
      </div>
      <span className={`text-[11px] mt-1 truncate max-w-full ${player.is_captain ? 'text-magenta font-semibold' : 'text-content'}`}>
        {player.name}
        {player.is_vice_captain && <span className="text-content-subtle"> (V)</span>}
      </span>
      <span className="text-[10px] text-content-subtle">
        {Number(player.predicted).toFixed(1)}
        {boosted && <span className="text-green-400"> ▲</span>}
        {faded && <span className="text-red-400"> ▼</span>}
      </span>
    </div>
  )
}

const PitchView: React.FC<{ squad: any; adjustments: Adjustment[] }> = ({ squad, adjustments }) => {
  const rows = POSITION_ORDER.map((pos) => ({
    pos,
    players: (squad.starting_xi || []).filter((p: any) => p.position === pos),
  })).filter((r) => r.players.length > 0)

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 text-sm">
        <span className="text-content-muted">
          Squad · {squad.formation} · <span className="text-content font-medium">{Number(squad.predicted_points).toFixed(1)} pts projected</span>
        </span>
        <span className="text-content-subtle">£{squad.total_cost}m · £{squad.remaining_budget}m bank</span>
      </div>

      <div className="rounded-xl border border-[#1f4030] bg-gradient-to-b from-[#12291c] to-[#0e1f16] px-2 py-4 space-y-4">
        {rows.map((row) => (
          <div key={row.pos} className="flex justify-center gap-2 sm:gap-5 flex-wrap">
            {row.players.map((p: any) => (
              <PlayerDot key={p.id} player={p} adjustment={adjustmentFor(adjustments, p.id)} />
            ))}
          </div>
        ))}
      </div>

      {squad.bench?.length > 0 && (
        <div className="flex items-center gap-2 mt-3 flex-wrap">
          <span className="text-xs text-content-subtle">Bench:</span>
          {squad.bench.map((p: any) => (
            <span
              key={p.id}
              title={`${p.name} (${p.team}) — ${Number(p.predicted).toFixed(1)} pts, £${p.price}m`}
              className="text-xs text-content-muted border border-border rounded-full px-2.5 py-0.5"
            >
              {p.name} <span className="text-content-subtle">{Number(p.predicted).toFixed(1)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ==================== Right column panels ====================

const CaptaincyPanel: React.FC<{
  ranking: any[]
  playerIndex: Map<number, any>
  squad: any
}> = ({ ranking, playerIndex, squad }) => {
  const xiIds = new Set<number>((squad?.starting_xi || []).map((p: any) => p.id))
  const actualCaptainId: number | undefined = squad?.captain?.id
  // The LLM's ranking often includes players outside the optimizer's £100m
  // squad — mark the actual squad captain, and flag out-of-squad picks as
  // "advisory" so the (C) badge always matches the pitch.
  const rows = ranking.slice(0, 5).map((c: any) => {
    const detail = playerIndex.get(c.id)
    const predicted = detail?.predicted != null ? Number(detail.predicted) : null
    return { ...c, predicted, inSquad: xiIds.has(c.id), isActualCaptain: c.id === actualCaptainId }
  })
  const max = Math.max(...rows.map((r) => r.predicted ?? 0), 0)
  const captainOutsideSquad = actualCaptainId != null && ranking.length > 0 && ranking[0].id !== actualCaptainId

  return (
    <div className="card">
      <h4 className="text-sm font-medium text-content-muted mb-3">Captaincy</h4>
      <div className="space-y-2.5">
        {rows.map((r, i) => {
          const width = r.predicted != null && max > 0 ? (r.predicted / max) * 100 : 100 - i * 15
          const highlight = r.isActualCaptain
          return (
            <div key={r.id} className={r.inSquad ? '' : 'opacity-60'}>
              <div className="flex justify-between text-xs mb-1">
                <span className={highlight ? 'text-magenta font-semibold' : 'text-content'}>
                  {highlight && '(C) '}
                  {r.name}
                  {!r.inSquad && (
                    <span className="text-content-subtle ml-1.5">· not in squad</span>
                  )}
                </span>
                {r.predicted != null && <span className="text-content-subtle">{r.predicted.toFixed(1)}</span>}
              </div>
              <div className="h-1.5 rounded-full bg-[#1a1a2e] overflow-hidden">
                <div
                  className={`h-full rounded-full ${highlight ? 'bg-magenta' : 'bg-[#4a4a6e]'}`}
                  style={{ width: `${Math.max(6, width)}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
      {captainOutsideSquad && (
        <p className="text-xs text-content-subtle mt-3 leading-relaxed">
          Hermes' top pick isn't in the £100m squad — {squad?.captain?.name} captains from the XI.
        </p>
      )}
    </div>
  )
}

const ChipPill: React.FC<{ label: string; playNow: boolean; detail?: string }> = ({ label, playNow, detail }) => (
  <div className="flex items-center justify-between gap-2 text-sm">
    <span className="text-content">{label}</span>
    <span
      className={`text-xs px-2.5 py-0.5 rounded-full whitespace-nowrap ${
        playNow
          ? 'bg-green-500/20 text-green-400 font-semibold'
          : 'bg-[#1a1a2e] text-content-subtle border border-border'
      }`}
    >
      {playNow ? 'Play now' : detail ? `Hold · ${detail}` : 'Hold'}
    </span>
  </div>
)

const ChipsPanel: React.FC<{ chipAdvice: any; tripleCaptain: any }> = ({ chipAdvice, tripleCaptain }) => {
  const targets = chipAdvice?.target_gameweeks || {}
  const target = (chip: string) => {
    const t = targets[chip]
    return t != null && t !== '' ? `GW${t}` : undefined
  }
  return (
    <div className="card">
      <h4 className="text-sm font-medium text-content-muted mb-3">Chips</h4>
      <div className="space-y-2">
        <ChipPill label="Wildcard" playNow={!!chipAdvice?.wildcard_now} detail={target('wildcard')} />
        <ChipPill label="Free Hit" playNow={!!chipAdvice?.free_hit_now} detail={target('free_hit')} />
        <ChipPill label="Bench Boost" playNow={!!chipAdvice?.bench_boost_now} detail={target('bench_boost')} />
        <ChipPill
          label="Triple Captain"
          playNow={!!tripleCaptain?.play_now}
          detail={tripleCaptain?.target_gameweek ? `GW${tripleCaptain.target_gameweek}` : undefined}
        />
      </div>
      {(chipAdvice?.reason?.trim() || tripleCaptain?.reason?.trim()) && (
        <p className="text-xs text-content-subtle mt-3 leading-relaxed">
          {chipAdvice?.reason?.trim() || tripleCaptain?.reason?.trim()}
        </p>
      )}
    </div>
  )
}

const AdjustmentsPanel: React.FC<{ adjustments: Adjustment[]; playerIndex: Map<number, any> }> = ({
  adjustments, playerIndex,
}) => {
  // Neutral (×1.0) entries are "looked at it, changed nothing" — skip the noise
  const rows = adjustments
    .filter((a) => Math.abs(a.multiplier - 1) >= 0.005)
    .sort((a, b) => Math.abs(b.multiplier - 1) - Math.abs(a.multiplier - 1))
  if (rows.length === 0) return null
  return (
    <div className="card">
      <h4 className="text-sm font-medium text-content-muted mb-1">What Hermes changed — and why</h4>
      <p className="text-xs text-content-subtle mb-3">
        The LLM's adjustments on top of the statistical model, from news, odds and game state.
      </p>
      <div className="space-y-2">
        {rows.map((a) => {
          const player = playerIndex.get(a.player_id)
          const pct = Math.round((a.multiplier - 1) * 100)
          const boost = pct > 0
          return (
            <div key={a.player_id} className="flex items-baseline gap-3 text-sm">
              <span className={`w-12 shrink-0 font-semibold ${boost ? 'text-green-400' : 'text-red-400'}`}>
                {boost ? '+' : ''}{pct}%
              </span>
              <span className="w-28 shrink-0 text-content font-medium truncate">
                {player?.name || `Player #${a.player_id}`}
              </span>
              <span className="text-content-muted text-xs sm:text-sm leading-snug">{a.reason}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const EvidencePanel: React.FC<{ signals: Record<string, AgentReport> }> = ({ signals }) => {
  const [openAgents, setOpenAgents] = useState<Record<string, boolean>>({})
  return (
    <div className="card">
      <h4 className="text-sm font-medium text-content-muted mb-3">Evidence — agent signals</h4>
      <div className="space-y-1">
        {Object.entries(signals).map(([name, r]) => {
          const open = openAgents[name]
          return (
            <div key={name} className="bg-bg rounded-lg border border-border">
              <button
                onClick={() => setOpenAgents((s) => ({ ...s, [name]: !s[name] }))}
                className="w-full flex items-center gap-2 p-3 text-left"
              >
                {open ? (
                  <ChevronDown className="w-4 h-4 text-content-subtle" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-content-subtle" />
                )}
                <span className="text-sm text-white">{AGENT_LABELS[name] || name}</span>
                {r.status === 'ok' ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
                ) : (
                  <AlertCircle
                    className={`w-3.5 h-3.5 ${r.status === 'degraded' ? 'text-yellow-400' : 'text-red-400'}`}
                  />
                )}
                <span className="text-xs text-content-subtle ml-auto">{r.elapsed_ms}ms</span>
              </button>
              {open && <div className="px-3 pb-3 text-sm text-content-muted">{r.summary}</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ==================== The page ====================

export interface ThisWeekTabProps {
  view: HermesRunType
  onViewChange: (view: HermesRunType) => void
  runs: Record<string, HermesRun | null>
  activeByType: Record<string, ActiveHermesRun>
  errors: Record<string, string | null>
  status: HermesStatus | null
  avgDurationMs: number | null
  onStart: (runType: HermesRunType, force?: boolean, fplTeamId?: number) => void
}

const ThisWeekTab: React.FC<ThisWeekTabProps> = ({
  view, onViewChange, runs, activeByType, errors, status, avgDurationMs, onStart,
}) => {
  const info: HermesRunTypeInfo = HERMES_RUN_TYPES.find((rt) => rt.value === view) || HERMES_RUN_TYPES[0]
  const run = runs[view] ?? null
  const active = activeByType[view] ?? null
  const error = errors[view] ?? null
  const isRunning = !!active
  const elapsed = useElapsed(active?.created_at, isRunning)
  const [showNarrative, setShowNarrative] = useState(false)
  const [team, setTeam] = useState(() => getStoredTeam())
  const [picksNote, setPicksNote] = useState(false)

  // my_team needs a connected team before Hermes can run
  const needsTeamSetup = view === 'my_team' && !team
  const start = (force?: boolean) => onStart(view, force, view === 'my_team' ? team?.id : undefined)

  const adjustments = useMemo(() => getAdjustments(run), [run])
  const playerIndex = useMemo(() => buildPlayerIndex(run), [run])

  const agentStates = run?.signals ? Object.values(run.signals) : []
  const okAgents = agentStates.filter((a) => a.status === 'ok').length

  return (
    <div className="space-y-4">
      {/* View switcher: the old run-type tabs folded into one page */}
      <div className="flex gap-1.5 overflow-x-auto scrollbar-hide pb-1">
        {HERMES_RUN_TYPES.map((rt) => (
          <button
            key={rt.value}
            onClick={() => onViewChange(rt.value)}
            className={`px-3 py-1.5 rounded-full text-xs whitespace-nowrap transition-colors border ${
              view === rt.value
                ? 'bg-[#00ff87]/10 text-[#00ff87] border-[#00ff87]/40'
                : 'text-content-muted border-border hover:text-content hover:border-border-strong'
            }`}
          >
            {rt.label}
            {activeByType[rt.value] && <Loader2 className="w-3 h-3 inline ml-1.5 animate-spin" />}
          </button>
        ))}
      </div>

      {status && !status.llm_configured && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-yellow-400 mt-0.5 shrink-0" />
          <div className="text-sm text-yellow-200">
            <p className="font-medium">Hermes LLM not configured — signals-only mode.</p>
            <p className="text-yellow-200/70">
              Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY on the backend to enable full reasoning.
            </p>
          </div>
        </div>
      )}

      {/* Verdict header */}
      {run && !isRunning && (
        <div className="card border-l-4 !border-l-[#00ff87]">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <p className="text-xs text-content-subtle mb-1">
                {run.gameweek ? `Gameweek ${run.gameweek}` : 'Pre-season'} · {info.label.toLowerCase()}
                {run.completed_at && <> · ran {formatRelative(run.completed_at)}</>}
              </p>
              <h2 className="text-lg sm:text-xl font-bold text-white leading-snug">{deriveVerdict(run)}</h2>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {agentStates.length > 0 && (
                <span
                  className={`text-xs px-2.5 py-1 rounded-full ${
                    okAgents === agentStates.length
                      ? 'bg-green-500/20 text-green-400'
                      : 'bg-yellow-500/20 text-yellow-400'
                  }`}
                >
                  {okAgents}/{agentStates.length} agents
                </span>
              )}
              <span
                className={`text-xs px-2.5 py-1 rounded-full ${
                  run.status === 'completed'
                    ? 'bg-green-500/20 text-green-400'
                    : run.status === 'degraded'
                    ? 'bg-yellow-500/20 text-yellow-400'
                    : 'bg-red-500/20 text-red-400'
                }`}
              >
                {run.status}
              </span>
            </div>
          </div>
          {run.error && <p className="text-sm text-red-400 mt-2">{run.error}</p>}
        </div>
      )}

      {/* My Team: setup gate, then a small "connected as" line */}
      {needsTeamSetup && (
        <TeamSetup
          onSaved={(t, picksAvailable) => {
            setTeam(t)
            setPicksNote(!picksAvailable)
          }}
        />
      )}
      {view === 'my_team' && team && (
        <div className="space-y-1">
          <p className="text-xs text-content-subtle">
            Connected: <span className="text-content">{team.name}</span> (#{team.id}) ·{' '}
            <button
              onClick={() => {
                localStorage.removeItem(TEAM_ID_KEY)
                localStorage.removeItem(TEAM_NAME_KEY)
                setTeam(null)
                setPicksNote(false)
              }}
              className="underline hover:text-content"
            >
              change team
            </button>
          </p>
          {picksNote && (
            <p className="text-xs text-yellow-400/90">
              Team connected — FPL publishes your picks once the first deadline passes, so squad
              analysis unlocks after the GW1 deadline.
            </p>
          )}
        </div>
      )}

      {/* Run controls */}
      {!needsTeamSetup && (
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={() => start(false)}
            disabled={isRunning}
            className="btn btn-hermes flex items-center gap-2"
          >
            {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            {isRunning ? 'Hermes is thinking…' : 'Ask Hermes'}
          </button>
          {!isRunning && run && (
            <button
              onClick={() => start(true)}
              className="text-sm text-content-muted hover:text-content transition-colors"
            >
              Re-run fresh
            </button>
          )}
          <span className="flex items-center gap-3 ml-auto">
            <ModelSwitcher />
            {status?.daily_briefing && (
              <span className="flex items-center gap-1.5 text-xs text-content-subtle">
                <Moon className="w-3.5 h-3.5" /> Nightly 03:30 UTC · fresh briefing 3h before deadline
              </span>
            )}
          </span>
        </div>
      )}

      {error && (
        <p className="text-sm text-red-400 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </p>
      )}

      {/* In-flight run */}
      {isRunning && (
        <div className="card">
          <div className="card-header">
            <div className="flex items-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-magenta" />
              <span>Running {info.label.toLowerCase()}…</span>
            </div>
          </div>
          <div className="mb-2">
            <div className="flex items-center justify-between text-xs text-content-muted mb-1">
              <span>Agents → LLM → optimizer</span>
              <span>{Math.round(active.progress)}%</span>
            </div>
            <div className="w-full bg-[#1a1a2e] rounded-full h-2 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-magenta to-purple-400 transition-all duration-500 ease-out"
                style={{ width: `${Math.max(2, active.progress)}%` }}
              />
            </div>
          </div>
          <p className="text-xs text-content-subtle">
            Elapsed {formatDuration(elapsed)}
            {avgDurationMs != null && <> · typically takes ~{formatDuration(avgDurationMs)}</>}
          </p>
        </div>
      )}

      {/* Report body */}
      {run && !isRunning && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            {run.result?.squad && (
              <div className="lg:col-span-3">
                <PitchView squad={run.result.squad} adjustments={adjustments} />
              </div>
            )}
            <div className={`space-y-4 ${run.result?.squad ? 'lg:col-span-2' : 'lg:col-span-5'}`}>
              {run.result?.captain_ranking?.length > 0 && (
                <CaptaincyPanel
                  ranking={run.result.captain_ranking}
                  playerIndex={playerIndex}
                  squad={run.result.squad}
                />
              )}
              {(run.result?.chip_advice || run.result?.triple_captain) && (
                <ChipsPanel chipAdvice={run.result.chip_advice} tripleCaptain={run.result.triple_captain} />
              )}
            </div>
          </div>

          {/* Transfers */}
          {run.result?.transfer_priorities?.length > 0 && (
            <div className="card">
              <h4 className="text-sm font-medium text-content-muted mb-2">Transfer priorities</h4>
              {run.result.transfer_priorities.map((t: any, i: number) => (
                <p key={i} className="text-sm text-content">
                  {t.out_name} → <span className="font-medium">{t.in_name}</span>{' '}
                  <span className="text-xs text-content-subtle">({t.urgency})</span> — {t.reason}
                </p>
              ))}
            </div>
          )}

          {/* Differentials */}
          {run.result?.differentials?.length > 0 && (
            <div className="card">
              <h4 className="text-sm font-medium text-content-muted mb-2">Differentials</h4>
              <div className="flex gap-2 flex-wrap">
                {run.result.differentials.map((d: any) => {
                  const detail = playerIndex.get(d.id)
                  return (
                    <span
                      key={d.id}
                      className="text-sm text-content border border-border rounded-full px-3 py-1"
                      title={detail?.ownership != null ? `${detail.ownership}% owned` : undefined}
                    >
                      {d.name}
                      {detail?.ownership != null && (
                        <span className="text-xs text-content-subtle ml-1.5">{detail.ownership}%</span>
                      )}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          {adjustments.length > 0 && <AdjustmentsPanel adjustments={adjustments} playerIndex={playerIndex} />}

          {run.signals && <EvidencePanel signals={run.signals} />}

          {/* Full narrative: the appendix, not the headline */}
          {run.narrative && (
            <div className="card">
              <button
                onClick={() => setShowNarrative((s) => !s)}
                className="w-full flex items-center gap-2 text-left"
              >
                {showNarrative ? (
                  <ChevronDown className="w-4 h-4 text-content-subtle" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-content-subtle" />
                )}
                <span className="text-sm font-medium text-content-muted">Full report</span>
                {run.model && (
                  <span className="text-xs text-content-subtle ml-auto">
                    {run.model} · {(run.prompt_tokens + run.completion_tokens).toLocaleString()} tokens
                  </span>
                )}
              </button>
              {showNarrative && (
                <div className="bg-bg rounded-lg border border-border p-4 mt-3">
                  <Narrative text={run.narrative} />
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Honest empty state — the nightly sweep or the button will fill it */}
      {!run && !isRunning && !needsTeamSetup && (
        <div className="card text-center py-12 text-content-muted">
          <Bot className="w-12 h-12 mx-auto mb-4 opacity-40" />
          <p>No {info.label.toLowerCase()} run yet.</p>
          <p className="text-xs mt-2">
            {view === 'my_team'
              ? 'My Team runs on demand — hit "Ask Hermes" above to analyze your squad.'
              : `Hermes runs every run type once a night${
                  status?.daily_briefing ? '' : ' (currently disabled on the backend)'
                } — or hit "Ask Hermes" above.`}
          </p>
        </div>
      )}
    </div>
  )
}

export default ThisWeekTab
