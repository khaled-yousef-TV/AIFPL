/**
 * This Week — the single Hermes report page, laid out as a matchday programme.
 *
 * Masthead (App) → poster hero carrying the verdict → numbered run-type nav →
 * run controls → a two-column plate: teamsheet-on-a-pitch and the adjustment
 * ledger on the left, captaincy / chips / agent signals on the right.
 *
 * The old per-run-type tabs are folded in as views: the briefing is the
 * default, and deep dives (wildcard rebuild, free hit, ...) render into the
 * same layout.
 */
import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
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
    <label
      className="flex items-center gap-2 text-[0.65rem] font-bold uppercase tracking-[0.12em] text-content-muted"
      title="Takes effect on the next run — no restart needed"
    >
      Model
      <select
        value={info.active ?? ''}
        disabled={switching}
        onChange={(e) => e.target.value && change(e.target.value)}
        className="field !py-1 !px-2 text-xs normal-case tracking-normal font-normal"
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
    <div>
      <h2 className="sec-h">Connect your FPL team</h2>
      <p className="text-sm text-content-muted mb-4 max-w-[60ch] leading-relaxed">
        Hermes analyzes your actual squad: transfers out/in, captaincy from your players, and
        chip timing for your situation. Find your team ID in the FPL site URL —{' '}
        <span className="text-content-subtle">fantasy.premierleague.com/entry/</span>
        <span className="text-accent font-semibold">1234567</span>
        <span className="text-content-subtle">/history</span>.
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !importing && importTeam()}
          placeholder="Team ID, e.g. 2321022"
          className="field w-48"
        />
        <button onClick={importTeam} disabled={importing} className="btn btn-primary">
          {importing && <Loader2 className="w-4 h-4 animate-spin" aria-hidden />}
          {importing ? 'Importing…' : 'Import team'}
        </button>
      </div>
      {error && (
        <p className="mt-3 text-sm text-danger flex items-center gap-2">
          <AlertCircle className="w-4 h-4" aria-hidden /> {error}
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
      <strong key={i} className="text-content font-bold">{part.slice(2, -2)}</strong>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    ),
  )
}

function Narrative({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <div className="space-y-1.5 text-sm text-content-muted leading-relaxed max-w-[72ch]">
      {lines.map((line, i) => {
        const t = line.trim()
        if (!t) return <div key={i} className="h-1" />
        if (t.startsWith('### ')) return <h4 key={i} className="text-content font-bold mt-3">{renderInline(t.slice(4))}</h4>
        if (t.startsWith('## ')) return <h3 key={i} className="text-content font-bold text-base mt-3">{renderInline(t.slice(3))}</h3>
        if (t.startsWith('# ')) return <h3 key={i} className="text-content font-bold text-base mt-3">{renderInline(t.slice(2))}</h3>
        if (/^[-*]\s/.test(t)) return <div key={i} className="flex gap-2 pl-1"><span className="text-accent">•</span><span>{renderInline(t.slice(2))}</span></div>
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

// ==================== Poster headline ====================

/**
 * The verdict is several sentences; the poster only shouts the first one and
 * runs the rest as the standfirst underneath.
 */
function splitVerdict(verdict: string): { head: string; rest: string } {
  const match = verdict.match(/^(.*?[.!?])\s+(.+)$/s)
  if (match) return { head: match[1], rest: match[2] }
  return { head: verdict, rest: '' }
}

/** First word in ink, the remainder in FPL purple. */
function twoTone(head: string): { lead: string; rest: string } {
  const trimmed = head.replace(/[.:]$/, '')
  const space = trimmed.indexOf(' ')
  if (space < 0) return { lead: trimmed, rest: '' }
  return { lead: trimmed.slice(0, space), rest: trimmed.slice(space + 1) }
}

function headlineSize(head: string): string {
  if (head.length <= 24) return 'sz-lg'
  if (head.length <= 48) return 'sz-md'
  return 'sz-sm'
}

/**
 * `split` two-tones the headline across two lines ("CAPTAIN" / "B.FERNANDES").
 * Titles that are already a single noun phrase — a run-type name on an empty
 * page — set it false, otherwise they break in the wrong place.
 */
const Headline: React.FC<{ text: string; sub?: string; split?: boolean }> = ({
  text, sub, split = true,
}) => {
  const { lead, rest } = split ? twoTone(text) : { lead: text, rest: '' }
  return (
    <>
      <h1 className={`hero-title ${headlineSize(text)}`}>
        <span className="lead">{lead}</span>
        {rest && (
          <>
            <br />
            <span className="rest">{rest}</span>
          </>
        )}
      </h1>
      {sub && <p className="hero-sub">{sub}</p>}
    </>
  )
}

// ==================== Pitch ====================

const POSITION_ORDER = ['GK', 'DEF', 'MID', 'FWD']

function adjustmentFor(adjustments: Adjustment[], playerId: number): Adjustment | undefined {
  return adjustments.find((a) => a.player_id === playerId)
}

const PlayerDot: React.FC<{ player: any; shirt: number; adjustment?: Adjustment }> = ({
  player, shirt, adjustment,
}) => {
  const boosted = adjustment && adjustment.multiplier > 1
  const faded = adjustment && adjustment.multiplier < 1
  const tooltip = [
    `${player.name} (${player.team}) — ${Number(player.predicted).toFixed(1)} pts, £${player.price}m`,
    player.opponent ? `${player.is_home ? 'vs' : '@'} ${player.opponent} (FDR ${player.difficulty})` : null,
    adjustment ? `Hermes ${adjustment.action} ×${adjustment.multiplier}: ${adjustment.reason}` : null,
    player.reason || null,
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <div className="pp" title={tooltip}>
      <div className={`dot ${player.is_captain ? 'dot-cap' : ''}`}>
        {player.team}
        <span className="dot-no">{shirt}</span>
        {player.is_captain && <span className="arm">C</span>}
        {!player.is_captain && player.is_vice_captain && <span className="arm arm-v">V</span>}
      </div>
      <div className={`pn ${player.is_captain ? 'pn-cap' : ''}`}>{player.name}</div>
      <div className="pv">
        {Number(player.predicted).toFixed(1)}
        {boosted && <i className="up"> ▲</i>}
        {faded && <i className="dn"> ▼</i>}
      </div>
    </div>
  )
}

const PitchView: React.FC<{ squad: any; adjustments: Adjustment[] }> = ({ squad, adjustments }) => {
  const rows = POSITION_ORDER.map((pos) => ({
    pos,
    players: (squad.starting_xi || []).filter((p: any) => p.position === pos),
  })).filter((r) => r.players.length > 0)

  // Shirt numbers run 1..11 down the sheet, keeping the teamsheet reading
  let shirt = 0

  return (
    <>
      <h2 className="sec-h">
        <span>Teamsheet</span>
        <b className="tabular">
          {squad.formation} · £{squad.total_cost}m · £{squad.remaining_budget}m bank
        </b>
      </h2>

      <div className="pitch">
        <div className="pitch-box" />
        {rows.map((row) => (
          <div key={row.pos} className="pitch-row">
            {row.players.map((p: any) => (
              <PlayerDot
                key={p.id}
                player={p}
                shirt={++shirt}
                adjustment={adjustmentFor(adjustments, p.id)}
              />
            ))}
          </div>
        ))}
      </div>

      {squad.bench?.length > 0 && (
        <div className="bench-row">
          <span>Bench</span>
          {squad.bench.map((p: any) => (
            <span
              key={p.id}
              title={`${p.name} (${p.team}) — ${Number(p.predicted).toFixed(1)} pts, £${p.price}m`}
              className="bench-chip tabular"
            >
              {p.name} {Number(p.predicted).toFixed(1)}
            </span>
          ))}
        </div>
      )}

      <div className="legend">
        <span><b className="text-success">▲</b> Hermes boosted</span>
        <span><b className="text-danger">▼</b> Hermes faded</span>
        <span><b className="text-accent">C</b> Captain</span>
        <span><b>V</b> Vice</span>
      </div>
    </>
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
    <>
      <h2 className="sec-h">Captaincy</h2>
      {rows.map((r, i) => {
        const width = r.predicted != null && max > 0 ? (r.predicted / max) * 100 : 100 - i * 15
        const highlight = r.isActualCaptain
        return (
          <div key={r.id} className="rank">
            <div className="rank-lab">
              <span className={highlight ? 'hi' : r.inSquad ? '' : 'off'}>
                {highlight && '(C) '}
                {r.name}
                {!r.inSquad && ' · not in squad'}
              </span>
              {r.predicted != null && <span className="val">{r.predicted.toFixed(1)}</span>}
            </div>
            <div className="bar">
              <i className={highlight ? 'hi' : ''} style={{ width: `${Math.max(6, width)}%` }} />
            </div>
          </div>
        )
      })}
      {captainOutsideSquad && (
        <p className="sec-note">
          Hermes' top pick isn't in the £100m squad — {squad?.captain?.name} captains from the XI.
        </p>
      )}
    </>
  )
}

const ChipBox: React.FC<{ label: string; playNow: boolean; detail?: string }> = ({
  label, playNow, detail,
}) => (
  <div className={`chip-box ${playNow ? 'play' : ''}`}>
    <b>{label}</b>
    <em>{playNow ? 'Play now' : detail ? `Hold · ${detail}` : 'Hold'}</em>
  </div>
)

const ChipsPanel: React.FC<{ chipAdvice: any; tripleCaptain: any }> = ({ chipAdvice, tripleCaptain }) => {
  const targets = chipAdvice?.target_gameweeks || {}
  const target = (chip: string) => {
    const t = targets[chip]
    return t != null && t !== '' ? `GW${t}` : undefined
  }
  return (
    <>
      <h2 className="sec-h">Chips</h2>
      <div className="chip-grid">
        <ChipBox label="Wildcard" playNow={!!chipAdvice?.wildcard_now} detail={target('wildcard')} />
        <ChipBox label="Free Hit" playNow={!!chipAdvice?.free_hit_now} detail={target('free_hit')} />
        <ChipBox label="Bench Boost" playNow={!!chipAdvice?.bench_boost_now} detail={target('bench_boost')} />
        <ChipBox
          label="Triple Capt."
          playNow={!!tripleCaptain?.play_now}
          detail={tripleCaptain?.target_gameweek ? `GW${tripleCaptain.target_gameweek}` : undefined}
        />
      </div>
      {(chipAdvice?.reason?.trim() || tripleCaptain?.reason?.trim()) && (
        <p className="sec-note">{chipAdvice?.reason?.trim() || tripleCaptain?.reason?.trim()}</p>
      )}
    </>
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
    <>
      <h2 className="sec-h sec-gap">
        <span>What Hermes changed</span>
        <b>{rows.length} adjustment{rows.length > 1 ? 's' : ''}</b>
      </h2>
      {rows.map((a) => {
        const player = playerIndex.get(a.player_id)
        const pct = Math.round((a.multiplier - 1) * 100)
        const boost = pct > 0
        return (
          <div key={a.player_id} className="ledger">
            <span className={`d ${boost ? 'up' : 'dn'}`}>{boost ? '+' : ''}{pct}%</span>
            <span className="n">{player?.name || `Player #${a.player_id}`}</span>
            <span className="r">{a.reason}</span>
          </div>
        )
      })}
    </>
  )
}

const EvidencePanel: React.FC<{ signals: Record<string, AgentReport> }> = ({ signals }) => {
  const [openAgents, setOpenAgents] = useState<Record<string, boolean>>({})
  const entries = Object.entries(signals)
  const ok = entries.filter(([, r]) => r.status === 'ok').length
  return (
    <>
      <h2 className="sec-h sec-gap">
        <span>Agent signals</span>
        <b className="tabular">{ok}/{entries.length}</b>
      </h2>
      {entries.map(([name, r]) => {
        const open = openAgents[name]
        return (
          <div key={name} className="border-b border-border last:border-b-0">
            <button
              onClick={() => setOpenAgents((s) => ({ ...s, [name]: !s[name] }))}
              className="w-full flex items-center gap-2 py-2 text-left text-[0.8125rem] font-semibold"
              aria-expanded={!!open}
            >
              {open ? (
                <ChevronDown className="w-3.5 h-3.5 text-content-subtle shrink-0" aria-hidden />
              ) : (
                <ChevronRight className="w-3.5 h-3.5 text-content-subtle shrink-0" aria-hidden />
              )}
              <span>{AGENT_LABELS[name] || name}</span>
              <span className="ml-auto flex items-center gap-2.5">
                <span className="font-mono text-[0.65rem] font-normal text-content-subtle tabular">
                  {r.elapsed_ms}ms
                </span>
                <span
                  className={`font-mono text-[0.65rem] font-bold ${
                    r.status === 'ok'
                      ? 'text-success'
                      : r.status === 'degraded'
                      ? 'text-warning'
                      : 'text-danger'
                  }`}
                >
                  {r.status === 'ok' ? 'OK' : r.status === 'degraded' ? 'DEGR' : 'FAIL'}
                </span>
              </span>
            </button>
            {open && <p className="pb-3 pl-5 text-sm text-content-muted leading-relaxed">{r.summary}</p>}
          </div>
        )
      })}
    </>
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
  const showReport = !!run && !isRunning
  const squad = run?.result?.squad

  // ---- hero copy, per state ----
  let kicker: string
  let headline: string
  let standfirst: string | undefined

  if (isRunning) {
    kicker = `${info.label} · running`
    headline = 'Hermes is thinking'
    standfirst = `Agents → LLM → optimizer. Elapsed ${formatDuration(elapsed)}${
      avgDurationMs != null ? ` · typically ~${formatDuration(avgDurationMs)}` : ''
    }.`
  } else if (run) {
    const verdict = deriveVerdict(run)
    const split = splitVerdict(verdict)
    kicker = [
      info.label,
      run.gameweek ? `Gameweek ${run.gameweek}` : 'Pre-season',
      run.completed_at ? `ran ${formatRelative(run.completed_at)}` : null,
      agentStates.length > 0 ? `${okAgents} of ${agentStates.length} agents` : null,
    ]
      .filter(Boolean)
      .join(' · ')
    headline = split.head
    standfirst = split.rest || undefined
  } else {
    kicker = `No ${info.label.toLowerCase()} run yet`
    headline = info.label
    standfirst =
      view === 'my_team'
        ? 'My Team runs on demand — hit "Ask Hermes" to analyze your squad.'
        : `Hermes runs every run type once a night${
            status?.daily_briefing ? '' : ' (currently disabled on the backend)'
          } — or hit "Ask Hermes" now.`
  }

  return (
    <div>
      {/* ---------- poster hero ---------- */}
      <section className="hero">
        <div className="min-w-0">
          <p className="hero-kicker">{kicker}</p>
          <Headline text={headline} sub={standfirst} split={showReport || isRunning} />
        </div>
        {showReport && squad && (
          <div className="hero-num">
            <b className="tabular">{Number(squad.predicted_points).toFixed(1)}</b>
            <em>Pts projected · {squad.formation}</em>
          </div>
        )}
        {isRunning && (
          <div className="hero-num">
            <b className="tabular">{Math.round(active.progress)}%</b>
            <em>Complete</em>
          </div>
        )}
      </section>

      {/* ---------- numbered run-type nav ---------- */}
      <nav className="run-nav scrollbar-hide" aria-label="Run type">
        {HERMES_RUN_TYPES.map((rt, i) => (
          <button
            key={rt.value}
            onClick={() => onViewChange(rt.value)}
            className="run-nav-item"
            aria-current={view === rt.value ? 'page' : undefined}
          >
            <i aria-hidden="true">{String(i + 1).padStart(2, '0')}</i>
            {rt.label}
            {activeByType[rt.value] && <Loader2 className="w-3 h-3 animate-spin" aria-hidden />}
          </button>
        ))}
      </nav>

      {/* ---------- run controls ---------- */}
      {!needsTeamSetup && (
        <div className="flex items-center gap-4 flex-wrap px-4 sm:px-6 py-3.5 border-b border-border">
          <button onClick={() => start(false)} disabled={isRunning} className="btn btn-hermes">
            {isRunning ? (
              <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="w-4 h-4" aria-hidden />
            )}
            {isRunning ? 'Hermes is thinking…' : 'Ask Hermes'}
          </button>
          {!isRunning && run && (
            <button onClick={() => start(true)} className="btn-link">
              Re-run fresh
            </button>
          )}
          <span className="flex items-center gap-4 ml-auto flex-wrap">
            <ModelSwitcher />
            {status?.daily_briefing && (
              <span className="hidden md:flex items-center gap-1.5 text-[0.65rem] font-bold uppercase tracking-[0.12em] text-content-subtle">
                <Moon className="w-3 h-3" aria-hidden /> Nightly 03:30 UTC
              </span>
            )}
          </span>
        </div>
      )}

      {/* ---------- notices ---------- */}
      <div className="px-4 sm:px-6 empty:hidden [&>*]:mt-4">
        {status && !status.llm_configured && (
          <div className="callout">
            <p className="font-bold">Hermes LLM not configured — signals-only mode.</p>
            <p className="text-content-muted">
              Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY on the backend to enable full reasoning.
            </p>
          </div>
        )}
        {error && (
          <p className="callout callout-bad flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" aria-hidden /> {error}
          </p>
        )}
        {run?.error && !isRunning && <p className="callout callout-bad">{run.error}</p>}
        {view === 'my_team' && team && (
          <p className="text-xs text-content-subtle">
            Connected: <span className="text-content font-semibold">{team.name}</span> (#{team.id}) ·{' '}
            <button
              onClick={() => {
                localStorage.removeItem(TEAM_ID_KEY)
                localStorage.removeItem(TEAM_NAME_KEY)
                setTeam(null)
                setPicksNote(false)
              }}
              className="underline underline-offset-2 hover:text-content"
            >
              change team
            </button>
          </p>
        )}
        {view === 'my_team' && team && picksNote && (
          <p className="callout">
            Team connected — FPL publishes your picks once the first deadline passes, so squad
            analysis unlocks after the GW1 deadline.
          </p>
        )}
      </div>

      {/* ---------- My Team setup gate ---------- */}
      {needsTeamSetup && (
        <div className="px-4 sm:px-6 py-5">
          <TeamSetup
            onSaved={(t, picksAvailable) => {
              setTeam(t)
              setPicksNote(!picksAvailable)
            }}
          />
        </div>
      )}

      {/* ---------- in-flight progress ---------- */}
      {isRunning && (
        <div className="px-4 sm:px-6 py-5">
          <h2 className="sec-h">
            <span>Running {info.label.toLowerCase()}</span>
            <b className="tabular">{Math.round(active.progress)}%</b>
          </h2>
          <div className="bar h-3">
            <i
              className="hi transition-[width] duration-300 ease-out"
              style={{ width: `${Math.max(2, active.progress)}%` }}
            />
          </div>
        </div>
      )}

      {/* ---------- the plate ---------- */}
      {showReport && (
        <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr]">
          <div className="px-4 sm:px-6 py-5 min-w-0">
            {squad && <PitchView squad={squad} adjustments={adjustments} />}

            {run.result?.transfer_priorities?.length > 0 && (
              <>
                <h2 className={`sec-h ${squad ? 'sec-gap' : ''}`}>
                  <span>Transfer priorities</span>
                  <b className="tabular">{run.result.transfer_priorities.length}</b>
                </h2>
                {run.result.transfer_priorities.map((t: any, i: number) => (
                  <div key={i} className="ledger">
                    <span className="d">{t.urgency}</span>
                    <span className="n">{t.out_name} →</span>
                    <span className="r">
                      <b className="text-content font-bold">{t.in_name}</b> — {t.reason}
                    </span>
                  </div>
                ))}
              </>
            )}

            {run.result?.differentials?.length > 0 && (
              <>
                <h2 className="sec-h sec-gap">Differentials</h2>
                <div className="flex gap-2 flex-wrap">
                  {run.result.differentials.map((d: any) => {
                    const detail = playerIndex.get(d.id)
                    return (
                      <span
                        key={d.id}
                        className="bench-chip"
                        title={detail?.ownership != null ? `${detail.ownership}% owned` : undefined}
                      >
                        {d.name}
                        {detail?.ownership != null && (
                          <span className="text-content-subtle"> {detail.ownership}%</span>
                        )}
                      </span>
                    )
                  })}
                </div>
              </>
            )}

            {adjustments.length > 0 && (
              <AdjustmentsPanel adjustments={adjustments} playerIndex={playerIndex} />
            )}

            {/* Full narrative: the appendix, not the headline */}
            {run.narrative && (
              <>
                <h2 className="sec-h sec-gap">
                  <span>Full report</span>
                  {run.model && (
                    <b className="tabular normal-case tracking-normal font-mono text-[0.65rem] font-normal text-content-subtle">
                      {run.model} · {(run.prompt_tokens + run.completion_tokens).toLocaleString()} tokens
                    </b>
                  )}
                </h2>
                <button
                  onClick={() => setShowNarrative((s) => !s)}
                  className="flex items-center gap-2 text-left btn-link !no-underline"
                  aria-expanded={showNarrative}
                >
                  {showNarrative ? (
                    <ChevronDown className="w-4 h-4" aria-hidden />
                  ) : (
                    <ChevronRight className="w-4 h-4" aria-hidden />
                  )}
                  {showNarrative ? 'Hide' : 'Read'} full report
                </button>
                {showNarrative && (
                  <div className="mt-3 border-l-2 border-border pl-4">
                    <Narrative text={run.narrative} />
                  </div>
                )}
              </>
            )}
          </div>

          <div className="px-4 sm:px-6 py-5 border-t lg:border-t-0 lg:border-l border-border min-w-0">
            {run.result?.captain_ranking?.length > 0 && (
              <CaptaincyPanel
                ranking={run.result.captain_ranking}
                playerIndex={playerIndex}
                squad={squad}
              />
            )}
            {(run.result?.chip_advice || run.result?.triple_captain) && (
              <div className={run.result?.captain_ranking?.length > 0 ? 'sec-gap' : ''}>
                <ChipsPanel chipAdvice={run.result.chip_advice} tripleCaptain={run.result.triple_captain} />
              </div>
            )}
            {run.signals && <EvidencePanel signals={run.signals} />}
          </div>
        </div>
      )}

      {/* ---------- honest empty state ---------- */}
      {!run && !isRunning && !needsTeamSetup && (
        <div className="px-4 sm:px-6 py-5 max-w-[70ch]">
          <h2 className="sec-h">About this run</h2>
          <p className="text-sm text-content-muted leading-relaxed">{info.description}</p>
          <h2 className="sec-h sec-gap">Other runs</h2>
          {HERMES_RUN_TYPES.filter((rt) => rt.value !== view).map((rt) => (
            <button
              key={rt.value}
              onClick={() => onViewChange(rt.value)}
              className="ledger w-full text-left hover:bg-surface-2 transition-colors"
            >
              <span className="n">{rt.label}</span>
              <span className="r">{rt.description}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default ThisWeekTab
