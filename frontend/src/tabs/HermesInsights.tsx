import React, { useEffect, useState } from 'react'
import { BarChart3, GraduationCap, History, Loader2 } from 'lucide-react'
import {
  BacktestSummary,
  CalibrationResponse,
  fetchArchiveStatus,
  fetchBacktest,
  fetchCalibration,
} from '../api/hermes'

/**
 * Hermes Insights: track record (calibration + lessons) and historical
 * strategy validation (backtest). Both degrade quietly when there's no
 * data yet (no evaluated runs / no season archive).
 */
const HermesInsights: React.FC = () => {
  const [calibration, setCalibration] = useState<CalibrationResponse | null>(null)
  const [seasons, setSeasons] = useState<Array<{ season: string; players: number }>>([])
  const [backtest, setBacktest] = useState<BacktestSummary | null>(null)
  const [loadingBacktest, setLoadingBacktest] = useState(false)

  useEffect(() => {
    fetchCalibration().then(setCalibration).catch(() => undefined)
    fetchArchiveStatus().then((r) => setSeasons(r.seasons)).catch(() => undefined)
  }, [])

  const runBacktest = async (season: string) => {
    setLoadingBacktest(true)
    try {
      setBacktest(await fetchBacktest(season))
    } catch {
      setBacktest(null)
    } finally {
      setLoadingBacktest(false)
    }
  }

  const profile = calibration?.profile
  const hasTrackRecord = (profile?.runs_scored ?? 0) > 0
  const lessons = calibration?.lessons ?? []

  return (
    <div className="card">
      <div className="card-header">
        <BarChart3 className="w-5 h-5 text-accent" />
        <span>Insights</span>
      </div>

      {/* Track record */}
      <div className="mb-5">
        <h4 className="text-sm font-medium text-content-muted mb-2 flex items-center gap-1.5">
          <History className="w-4 h-4" /> Track record
        </h4>
        {hasTrackRecord ? (
          <div className="space-y-2">
            <p className="text-xs text-content-subtle">
              Scored over {profile!.runs_scored} run(s)
              {profile!.captain_regret_avg != null && (
                <> · avg captain regret <span className="tabular text-content">{profile!.captain_regret_avg}</span> pts</>
              )}
            </p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(profile!.action_hit_rates).map(([action, rate]) => (
                <span key={action} className="pill pill-info tabular">
                  {action}: {Math.round(rate * 100)}%
                  <span className="text-content-subtle ml-1">(trust {profile!.trust_weights[action]})</span>
                </span>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-xs text-content-subtle">
            No evaluated runs yet — track record builds after gameweeks finish and the learning cycle scores Hermes' calls.
          </p>
        )}
      </div>

      {/* Lessons */}
      {lessons.length > 0 && (
        <div className="mb-5">
          <h4 className="text-sm font-medium text-content-muted mb-2 flex items-center gap-1.5">
            <GraduationCap className="w-4 h-4" /> Lessons learned
          </h4>
          <ul className="space-y-1">
            {lessons.slice(0, 6).map((l) => (
              <li key={l.id} className="text-sm text-content-muted flex gap-2">
                <span className="text-magenta">•</span>
                <span><span className="text-content-subtle">[{l.category}]</span> {l.lesson}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Backtest */}
      <div>
        <h4 className="text-sm font-medium text-content-muted mb-2">Strategy validation (backtest)</h4>
        {seasons.length === 0 ? (
          <p className="text-xs text-content-subtle">
            No archived season yet. Run <code className="text-accent">POST /api/hermes/archive-season</code> before
            the FPL API resets to enable backtesting and cold-start priors.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {seasons.map((s) => (
                <button
                  key={s.season}
                  onClick={() => runBacktest(s.season)}
                  disabled={loadingBacktest}
                  className="btn btn-secondary text-xs flex items-center gap-1.5"
                >
                  {loadingBacktest ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                  Backtest {s.season} ({s.players} players)
                </button>
              ))}
            </div>
            {backtest && (
              <div className="bg-bg rounded-lg border border-border p-3 text-sm space-y-2">
                <p className="text-content-subtle text-xs">
                  {backtest.summary.gameweeks_scored} gameweeks scored
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <Stat label="Captain (blend)" value={backtest.summary.captaincy.by_blend_avg} suffix=" pts" />
                  <Stat label="Captain (mean)" value={backtest.summary.captaincy.by_mean_avg} suffix=" pts" />
                  <Stat label="Best possible" value={backtest.summary.captaincy.best_possible_avg} suffix=" pts" />
                  <Stat label="Hot-form edge" value={backtest.summary.form_signal.edge_vs_league} suffix=" pts/GW" signed />
                  <Stat label="Hot-form avg" value={backtest.summary.form_signal.hot_top10_avg} suffix=" pts" />
                  <Stat label="League avg" value={backtest.summary.form_signal.league_avg} suffix=" pts" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

const Stat: React.FC<{ label: string; value: number; suffix?: string; signed?: boolean }> = ({
  label, value, suffix = '', signed = false,
}) => (
  <div className="bg-surface rounded-lg p-2">
    <div className="text-[11px] text-content-subtle">{label}</div>
    <div className={`text-base font-semibold tabular ${signed ? (value >= 0 ? 'text-success' : 'text-danger') : 'text-content'}`}>
      {signed && value >= 0 ? '+' : ''}{value}{suffix}
    </div>
  </div>
)

export default HermesInsights
