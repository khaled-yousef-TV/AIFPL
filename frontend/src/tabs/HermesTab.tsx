import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Sparkles,
} from 'lucide-react'
import {
  AgentReport,
  HermesRun,
  HermesRunType,
  HermesStatus,
  fetchHermesRun,
  fetchHermesStatus,
  fetchLatestHermesRun,
  startHermesRun,
} from '../api/hermes'
import HermesInsights from './HermesInsights'

const RUN_TYPES: { value: HermesRunType; label: string; description: string }[] = [
  { value: 'briefing', label: 'Weekly Briefing', description: 'Full analysis: squad, captaincy, chips, differentials' },
  { value: 'squad', label: 'Best Squad', description: 'Optimal 15-man squad with Hermes adjustments' },
  { value: 'wildcard', label: 'Wildcard', description: 'Should you wildcard now? Full rebuild plan' },
  { value: 'free_hit', label: 'Free Hit', description: 'One-week-only optimal squad' },
  { value: 'triple_captain', label: 'Triple Captain', description: 'Highest-ceiling captaincy for TC' },
  { value: 'differentials', label: 'Differentials', description: 'Low-ownership picks with strong signals' },
]

const POLL_INTERVAL_MS = 3000
const TERMINAL_STATES = ['completed', 'degraded', 'failed']

const AGENT_LABELS: Record<string, string> = {
  data: 'Data',
  mechanics: 'Game Mechanics',
  availability: 'Availability',
  form: 'Form',
  variability: 'Variability',
  betting: 'Betting Market',
  news: 'News & Sentiment',
}

/** Render inline **bold** within a line of text. */
function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i} className="text-content font-semibold">{part.slice(2, -2)}</strong>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    ),
  )
}

/** Minimal, dependency-free markdown renderer for Hermes' narrative. */
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

const HermesTab: React.FC = () => {
  const [status, setStatus] = useState<HermesStatus | null>(null)
  const [runType, setRunType] = useState<HermesRunType>('briefing')
  const [run, setRun] = useState<HermesRun | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openAgents, setOpenAgents] = useState<Record<string, boolean>>({})
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const pollRun = useCallback((runId: string) => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const updated = await fetchHermesRun(runId)
        setRun(updated)
        if (TERMINAL_STATES.includes(updated.status)) stopPolling()
      } catch {
        // transient polling errors are fine; keep trying
      }
    }, POLL_INTERVAL_MS)
  }, [])

  useEffect(() => {
    fetchHermesStatus().then(setStatus).catch(() => setStatus(null))
    fetchLatestHermesRun().then(setRun).catch(() => undefined)
    return stopPolling
  }, [])

  const isRunning = !!run && !TERMINAL_STATES.includes(run.status)

  const handleAsk = async (force = false) => {
    setError(null)
    setStarting(true)
    try {
      const started = await startHermesRun(runType, { force })
      const current = await fetchHermesRun(started.run_id)
      setRun(current)
      if (!started.cached && !TERMINAL_STATES.includes(current.status)) {
        pollRun(started.run_id)
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to start Hermes run')
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Status banner */}
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

      {/* Run controls */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-magenta" />
            <span>Ask Hermes</span>
            {status?.model && (
              <span className="text-xs text-content-subtle ml-2">({status.model})</span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-4">
          {RUN_TYPES.map((rt) => (
            <button
              key={rt.value}
              onClick={() => setRunType(rt.value)}
              className={`text-left p-3 rounded-lg border transition-colors ${
                runType === rt.value
                  ? 'border-magenta bg-magenta/10'
                  : 'border-border bg-bg hover:border-magenta/50'
              }`}
            >
              <div className="text-sm font-medium text-white">{rt.label}</div>
              <div className="text-xs text-content-muted mt-0.5">{rt.description}</div>
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => handleAsk(false)}
            disabled={starting || isRunning}
            className="btn btn-hermes flex items-center gap-2"
          >
            {starting || isRunning ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Sparkles className="w-4 h-4" />
            )}
            {isRunning ? 'Hermes is thinking…' : 'Ask Hermes'}
          </button>
          {run && TERMINAL_STATES.includes(run.status) && (
            <button
              onClick={() => handleAsk(true)}
              disabled={starting || isRunning}
              className="text-sm text-content-muted hover:text-content transition-colors"
            >
              Re-run fresh
            </button>
          )}
        </div>

        {error && (
          <p className="mt-3 text-sm text-red-400 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" /> {error}
          </p>
        )}
      </div>

      {/* Run result */}
      {run && (
        <div className="card">
          <div className="card-header">
            <div className="flex items-center justify-between w-full">
              <div className="flex items-center gap-2">
                <Bot className="w-5 h-5 text-accent" />
                <span>
                  {RUN_TYPES.find((r) => r.value === run.run_type)?.label || run.run_type} — GW
                  {run.gameweek}
                </span>
              </div>
              <span
                className={`text-xs font-medium px-2 py-1 rounded ${
                  run.status === 'completed'
                    ? 'bg-green-500/20 text-green-400'
                    : run.status === 'degraded'
                    ? 'bg-yellow-500/20 text-yellow-400'
                    : run.status === 'failed'
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-cyan-500/20 text-accent'
                }`}
              >
                {run.status}
              </span>
            </div>
          </div>

          {isRunning && (
            <div className="text-center py-10 text-content-muted">
              <Loader2 className="w-10 h-10 mx-auto mb-3 animate-spin text-magenta" />
              <p>Running agents and reasoning over the signals…</p>
              <p className="text-xs mt-1">This can take a minute (LLM + 7 agents).</p>
            </div>
          )}

          {run.error && (
            <p className="text-sm text-red-400 mb-4">{run.error}</p>
          )}

          {/* Narrative */}
          {run.narrative && !isRunning && (
            <div className="bg-bg rounded-lg border border-border p-4 mb-4">
              <Narrative text={run.narrative} />
            </div>
          )}

          {/* Captain ranking */}
          {run.result?.captain_ranking?.length > 0 && !isRunning && (
            <div className="mb-4">
              <h4 className="text-sm font-medium text-content-muted mb-2">Captaincy ranking</h4>
              <ol className="space-y-1">
                {run.result.captain_ranking.slice(0, 5).map((c: any, i: number) => (
                  <li key={c.id} className="text-sm text-content">
                    <span className="text-magenta font-medium">{i + 1}.</span> {c.name}
                    {i === 0 && <span className="ml-2 text-xs text-magenta">(C)</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Optimized squad */}
          {run.result?.squad && !isRunning && (
            <div className="mb-4">
              <h4 className="text-sm font-medium text-content-muted mb-2">
                Optimized squad ({run.result.squad.formation}) —{' '}
                {run.result.squad.predicted_points} projected pts
              </h4>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <div className="bg-bg rounded-lg border border-border p-3">
                  <p className="text-xs text-content-subtle mb-2">Starting XI</p>
                  {run.result.squad.starting_xi?.map((p: any) => (
                    <div key={p.id} className="text-sm text-content flex justify-between">
                      <span>
                        {p.name}
                        {p.is_captain && <span className="text-magenta"> (C)</span>}
                        {p.is_vice_captain && <span className="text-content-muted"> (V)</span>}
                      </span>
                      <span className="text-content-subtle">{Number(p.predicted).toFixed(1)}</span>
                    </div>
                  ))}
                </div>
                <div className="bg-bg rounded-lg border border-border p-3">
                  <p className="text-xs text-content-subtle mb-2">Bench</p>
                  {run.result.squad.bench?.map((p: any) => (
                    <div key={p.id} className="text-sm text-content-muted flex justify-between">
                      <span>{p.name}</span>
                      <span className="text-content-subtle">{Number(p.predicted).toFixed(1)}</span>
                    </div>
                  ))}
                  <p className="text-xs text-content-subtle mt-3">
                    Cost: £{run.result.squad.total_cost}m · Bank: £{run.result.squad.remaining_budget}m
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Chip advice */}
          {run.result?.chip_advice && !isRunning && (
            <div className="mb-4">
              <h4 className="text-sm font-medium text-content-muted mb-2">Chip advice</h4>
              <p className="text-sm text-content">{run.result.chip_advice.reason}</p>
            </div>
          )}

          {/* Transfer priorities */}
          {run.result?.transfer_priorities?.length > 0 && !isRunning && (
            <div className="mb-4">
              <h4 className="text-sm font-medium text-content-muted mb-2">Transfer priorities</h4>
              {run.result.transfer_priorities.map((t: any, i: number) => (
                <p key={i} className="text-sm text-content">
                  {t.out_name} → {t.in_name}{' '}
                  <span className="text-xs text-content-subtle">({t.urgency})</span> — {t.reason}
                </p>
              ))}
            </div>
          )}

          {/* Agent signals (collapsible) */}
          {run.signals && !isRunning && (
            <div>
              <h4 className="text-sm font-medium text-content-muted mb-2">Agent signals</h4>
              <div className="space-y-1">
                {Object.entries(run.signals).map(([name, report]) => {
                  const r = report as AgentReport
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
                            className={`w-3.5 h-3.5 ${
                              r.status === 'degraded' ? 'text-yellow-400' : 'text-red-400'
                            }`}
                          />
                        )}
                        <span className="text-xs text-content-subtle ml-auto">{r.elapsed_ms}ms</span>
                      </button>
                      {open && (
                        <div className="px-3 pb-3 text-sm text-content-muted">{r.summary}</div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Track record + backtest insights */}
      <HermesInsights />
    </div>
  )
}

export default HermesTab
