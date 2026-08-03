/**
 * Hermes accountability view — "is it actually learning?"
 *
 * Surfaces the per-model calibration profile and active lessons. Right now
 * that machinery is invisible behind the scenes; giving it a page makes the
 * question of Hermes's edge answerable at a glance, and makes model-switch
 * continuity (fresh trust weights on switch) visible instead of hidden.
 */
import React, { useEffect, useState } from 'react'
import { fetchCalibration, type CalibrationResponse } from '../api/hermes'

const HermesAccountability: React.FC = () => {
  const [data, setData] = useState<CalibrationResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetchCalibration().then(setData).catch((e) => setErr(e?.message || 'Failed to load calibration'))
  }, [])

  if (err) return <p className="sec-note text-danger px-4 sm:px-6 py-5">{err}</p>
  if (!data) return <p className="sec-note text-content-subtle px-4 sm:px-6 py-5">Loading…</p>

  const runs = data.profile.runs_scored
  const trust = data.profile.trust_weights || {}
  const hitRates = data.profile.action_hit_rates || {}
  const samples = data.profile.action_samples || {}
  const regret = data.profile.captain_regret_avg

  return (
    <div className="px-4 sm:px-6 py-5">
      <h2 className="sec-h">
        <span>Active model</span>
        <b className="tabular normal-case">{data.model ?? 'none (legacy config)'}</b>
      </h2>
      <p className="sec-note text-content-subtle">
        Trust weights and calibration are per-model. Switching provider resets these
        numbers so a new model doesn't inherit the outgoing one's mistakes.
      </p>

      <h2 className="sec-h sec-gap">
        <span>Track record</span>
        <b className="tabular">{runs} scored run{runs === 1 ? '' : 's'}</b>
      </h2>
      {runs === 0 ? (
        <p className="sec-note">
          No scored runs for <b>{data.model ?? 'this model'}</b> yet. Hit-rates and trust
          weights start populating after finished gameweeks are evaluated (daily job at
          06:00 UTC).
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1">
            {['boost', 'fade', 'exclude', 'lock'].map((action) => {
              const rate = hitRates[action]
              const n = samples[action]
              const w = trust[action]
              if (n == null || n === 0) return null
              return (
                <div key={action} className="ledger">
                  <span className="d">{action}</span>
                  <span className="n tabular">
                    {rate != null ? `${Math.round(rate * 100)}%` : '—'} hit
                    <span className="text-content-subtle"> · {n} samples</span>
                  </span>
                  <span className="r tabular">
                    trust {w != null ? w.toFixed(2) : '—'}
                  </span>
                </div>
              )
            })}
          </div>
          {regret != null && (
            <p className="sec-note sec-gap">
              Avg captaincy regret: <b className="tabular">{regret.toFixed(2)} pts</b> vs the
              best candidate. Zero = perfect pick every time.
            </p>
          )}
        </>
      )}

      <h2 className="sec-h sec-gap">
        <span>Active lessons</span>
        <b className="tabular">{data.lessons.length}</b>
      </h2>
      {data.lessons.length === 0 ? (
        <p className="sec-note">
          No active lessons. Lessons are distilled by an LLM pass after each finished
          gameweek and decay 10% per week (dropped once weight &lt; 0.3, roughly 11 GWs).
          Game-fact lessons ("promoted teams leak in GW1-2") survive a model switch;
          self-calibration lessons ("your boosts are overconfident") don't.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {data.lessons.map((l) => (
            <div key={l.id} className="ledger">
              <span className="d">{l.category}</span>
              <span className="n">{l.lesson}</span>
              <span className="r tabular text-content-subtle">
                w{l.weight.toFixed(2)} · {l.scope ?? 'model'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default HermesAccountability
