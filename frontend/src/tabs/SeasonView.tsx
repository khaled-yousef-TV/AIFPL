/**
 * Season view — the long-term outlook.
 *
 * Renders the tracked-squad ledger vs the FPL template average, the latest
 * season_plan Hermes narrative, and links to run one if none exists. All
 * three signals already exist in the backend; this page is the first place
 * they've had to live together.
 */
import React, { useEffect, useState } from 'react'
import { AlertCircle, Sparkles } from 'lucide-react'
import { fetchLatestHermesRun, type HermesRun } from '../api/hermes'
import { fetchTrackedSquad, type TrackedSquadResponse } from '../api/tracked-squad'

const SeasonView: React.FC<{ onOpenScenario: (runType: 'squad' | 'wildcard' | 'free_hit') => void }> = () => {
  const [tracked, setTracked] = useState<TrackedSquadResponse | null>(null)
  const [seasonPlan, setSeasonPlan] = useState<HermesRun | null | 'none'>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetchTrackedSquad().then(setTracked).catch((e) => setErr(e?.message || 'Failed to load ledger'))
    fetchLatestHermesRun('season_plan' as any).then(setSeasonPlan).catch(() => setSeasonPlan('none'))
  }, [])

  const ledger = tracked?.ledger || []
  const scored = ledger.filter((r) => r.points_scored != null)
  const cumulative = scored.reduce((sum, r) => sum + (r.points_scored ?? 0) - r.transfer_cost, 0)
  const templateCumulative = scored.reduce((sum, r) => sum + (r.average_score ?? 0), 0)
  const vsTemplate = scored.length > 0 && templateCumulative > 0 ? cumulative - templateCumulative : null

  return (
    <div className="px-4 sm:px-6 py-5">
      {err && (
        <p className="sec-note text-danger">
          <AlertCircle className="w-3.5 h-3.5 inline mr-1 -mt-0.5" aria-hidden /> {err}
        </p>
      )}

      {/* ---------- Ledger summary ---------- */}
      <h2 className="sec-h">
        <span>Season ledger</span>
        {scored.length > 0 && (
          <b className="tabular">
            {cumulative} pts · {scored.length} GW{scored.length !== 1 ? 's' : ''}
            {vsTemplate != null && (
              <>
                {' · '}
                <span className={vsTemplate >= 0 ? 'text-success' : 'text-danger'}>
                  {vsTemplate >= 0 ? '+' : ''}{vsTemplate} vs template
                </span>
              </>
            )}
          </b>
        )}
      </h2>

      {tracked?.seeded ? (
        scored.length > 0 ? (
          <div className="flex flex-col gap-1">
            {scored.map((row) => {
              const net = (row.points_scored ?? 0) - row.transfer_cost
              const avg = row.average_score ?? null
              return (
                <div key={row.gameweek} className="ledger">
                  <span className="d tabular">GW{row.gameweek}</span>
                  <span className="n">
                    {row.points_scored} pts
                    {row.transfer_cost > 0 && <span className="text-danger"> (−{row.transfer_cost})</span>}
                  </span>
                  <span className="r tabular">
                    {avg != null && (
                      <>
                        template {avg}{' · '}
                        <span className={net - avg >= 0 ? 'text-success' : 'text-danger'}>
                          {net - avg >= 0 ? '+' : ''}{net - avg}
                        </span>
                      </>
                    )}
                  </span>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="sec-note">
            No gameweeks scored yet. The ledger fills in automatically after each finished GW.
          </p>
        )
      ) : (
        <p className="sec-note">
          The tracked squad isn't seeded yet, so there's no ledger. Head to the{' '}
          <b>Squad</b> tab to seed it from a Best Squad run.
        </p>
      )}

      {/* ---------- Season plan ---------- */}
      <h2 className="sec-h sec-gap">
        <span>Season plan</span>
        {seasonPlan && seasonPlan !== 'none' && seasonPlan.gameweek > 0 && (
          <b className="tabular">GW{seasonPlan.gameweek} · {seasonPlan.model}</b>
        )}
      </h2>
      {seasonPlan === null && <p className="sec-note">Loading…</p>}
      {seasonPlan === 'none' && (
        <p className="sec-note">
          No season plan run yet. Season Plan is a rolling long-horizon strategy (chip
          windows, fixture swings, transfers to line up over the next 5+ GWs) — trigger
          it from the Weekly Briefing nightly sweep or ask a project owner to enable it.
        </p>
      )}
      {seasonPlan && seasonPlan !== 'none' && seasonPlan.narrative && (
        <div className="mt-3 border-l-2 border-border pl-4 prose-editorial">
          {seasonPlan.narrative.split('\n').filter(Boolean).map((line, i) => (
            <p key={i} className="sec-note">{line}</p>
          ))}
        </div>
      )}

      <p className="sec-note sec-gap text-content-subtle">
        <Sparkles className="w-3.5 h-3.5 inline mr-1 -mt-0.5" aria-hidden />
        The template baseline is FPL's average points per gameweek — the number every
        manager beats or loses against on a given week. Comparing here isolates Hermes's
        edge from the noise of "everyone hauled" gameweeks.
      </p>
    </div>
  )
}

export default SeasonView
