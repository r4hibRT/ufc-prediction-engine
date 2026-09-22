import { useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ComposedChart, Area, Line, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { api, useApi } from '../api';

const num = (v, dp = 0) => (v === null || v === undefined ? '—' : Number(v).toFixed(dp));

function ageFrom(dob) {
  if (!dob) return null;
  const d = new Date(dob);
  return Math.floor((Date.now() - d.getTime()) / (365.25 * 24 * 3600 * 1000));
}

function TrajectoryTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tip">
      <div className="tip-date">{p.date}</div>
      <div className="tip-op">
        <span className={`res res-${p.result}`}>{p.result}</span> vs {p.opponent}
      </div>
      <div className="tip-meta">
        {p.method}
        {p.is_title_fight ? (p.is_defence ? ' · title defence' : ' · title fight') : ''}
      </div>
      <div className="tip-rating">
        {p.rating.toFixed(0)} <span>± {(2 * p.rd).toFixed(0)}</span>
      </div>
    </div>
  );
}

// A custom shape is invoked for empty rows too, so each marker checks its own
// key or the unused series pile up along the top of the axis.
const marker = (key, render) => (props) =>
  props?.payload?.[key] == null ? null : render(props);

const WinDot = marker('win', ({ cx, cy }) => (
  <circle cx={cx} cy={cy} r={4.5} fill="var(--text)" />
));

const LossDot = marker('loss', ({ cx, cy }) => (
  <circle cx={cx} cy={cy} r={4.5} fill="var(--surface)"
          stroke="var(--text-dim)" strokeWidth={1.8} />
));

const OtherDot = marker('other', ({ cx, cy }) => (
  <circle cx={cx} cy={cy} r={3.5} fill="var(--text-dim)" />
));


function Stat({ label, value, sub }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <b>{value}</b>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

export default function Fighter() {
  const { id } = useParams();
  const profile = useApi(() => api.fighter(id), [id]);
  const career = useApi(() => api.career(id).catch(() => []), [id]);
  const stats = useApi(() => api.stats(id), [id]);

  const series = useMemo(() => {
    const arc = career.data || [];
    return arc.map((p) => {
      const rating = Number(p.rating);
      const rd = Number(p.rd);
      return {
        ...p,
        rating,
        rd,
        // Recharts stacks an Area from a [low, high] tuple, which is how the
        // uncertainty band is drawn behind the line.
        band: [rating - 2 * rd, rating + 2 * rd],
        win: p.result === 'win' ? rating : null,
        loss: p.result === 'loss' ? rating : null,
        other: p.result !== 'win' && p.result !== 'loss' ? rating : null,
      };
    });
  }, [career.data]);

  if (profile.loading) return <div className="state">Loading fighter…</div>;
  if (profile.error) return <div className="state error">{profile.error}</div>;

  const f = profile.data;
  const rec = f.record || {};
  const rates = stats.data?.rates || {};
  const entering = stats.data?.entering_last_bout;

  const recordLine = [
    `${rec.wins}-${rec.losses}${rec.draws ? `-${rec.draws}` : ''}`,
    rec.no_contests ? `${rec.no_contests} NC` : null,
  ].filter(Boolean).join(', ');

  return (
    <>
      <div className="page-head">
        <Link to="/fighters" className="backlink">&larr; All fighters</Link>
        <h1>{f.name}</h1>
        <p>
          {f.division} · {recordLine}
          {rec.title_fights ? ` · ${rec.title_fights} title fights, ${rec.title_defences} defences` : ''}
        </p>
      </div>

      <div className="stat-row">
        <Stat label="Current rating" value={num(f.current_rating)}
              sub={f.last_rated ? `as of ${f.last_rated}` : null} />
        <Stat label="Peak rating" value={num(f.peak_rating)}
              sub={f.peak_date ? `on ${f.peak_date}` : null} />
        <Stat label="Uncertainty" value={`± ${num(f.current_rd * 2)}`} sub="95% interval" />
        <Stat label="Bouts" value={rec.appearances ?? '—'}
              sub={`${rec.ko_wins}KO / ${rec.sub_wins}SUB / ${rec.dec_wins}DEC`} />
        <Stat label="Physicals"
              value={`${num(f.height)}" / ${num(f.reach)}"`}
              sub={`${f.stance || '—'}${ageFrom(f.dob) ? ` · ${ageFrom(f.dob)}y` : ''}`} />
      </div>

      <h2 className="section-title">Rating trajectory</h2>
      {career.loading && <div className="state">Loading career…</div>}
      {!career.loading && series.length === 0 && (
        <div className="state">No rated bouts for this fighter.</div>
      )}
      {series.length > 0 && (
        <>
          <div className="chart-card">
            <ResponsiveContainer width="100%" height={340}>
              <ComposedChart data={series} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-dim)' }}
                       tickLine={false} axisLine={{ stroke: 'var(--line)' }} minTickGap={40} />
                <YAxis domain={['dataMin - 40', 'dataMax + 40']}
                       tickFormatter={(v) => Math.round(v)}
                       tick={{ fontSize: 11, fill: 'var(--text-dim)' }}
                       tickLine={false} axisLine={false} width={54} />
                <ReferenceLine y={1500} stroke="var(--line-strong)" strokeDasharray="3 3"
                               label={{ value: 'DEBUT 1500', position: 'insideBottomLeft',
                                        fontSize: 10, fill: 'var(--text-dim)' }} />
                <Tooltip content={<TrajectoryTooltip />} />
                <Area dataKey="band" stroke="none" fill="var(--red)" fillOpacity={0.13}
                      isAnimationActive={false} />
                <Line dataKey="rating" stroke="var(--red)" strokeWidth={2.5} dot={false}
                      isAnimationActive={false} />
                <Scatter dataKey="win" shape={<WinDot />} isAnimationActive={false} />
                <Scatter dataKey="loss" shape={<LossDot />} isAnimationActive={false} />
                <Scatter dataKey="other" shape={<OtherDot />} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="footnote">
            The shaded band is the 95% rating interval. It widens after a layoff
            and narrows with frequent bouts, so a flat rating may still be getting
            more or less certain. Solid markers are wins, hollow rings losses.
          </p>
        </>
      )}

      <h2 className="section-title">Career statistics</h2>
      {stats.loading && <div className="state">Loading statistics…</div>}
      {stats.data && (
        <div className="stat-grid">
          <Stat label="Cage time" value={`${num(rates.minutes_fought, 0)} min`} />
          <Stat label="Sig. strikes / min" value={num(rates.sig_strikes_per_min, 2)} />
          <Stat label="Absorbed / min" value={num(rates.sig_absorbed_per_min, 2)} />
          <Stat label="Striking differential" value={num(rates.striking_differential, 2)} />
          <Stat label="Strike accuracy" value={rates.sig_accuracy ? `${(rates.sig_accuracy * 100).toFixed(0)}%` : '—'} />
          <Stat label="Takedowns / 15min" value={num(rates.takedowns_per_15, 2)} />
          <Stat label="Takedown accuracy" value={rates.takedown_accuracy ? `${(rates.takedown_accuracy * 100).toFixed(0)}%` : '—'} />
          <Stat label="Takedown defence" value={rates.takedown_defence ? `${(rates.takedown_defence * 100).toFixed(0)}%` : '—'} />
          <Stat label="Control share" value={rates.control_share ? `${(rates.control_share * 100).toFixed(0)}%` : '—'} />
          <Stat label="Knockdowns / 15min" value={num(rates.knockdowns_per_15, 2)} />
          <Stat label="Sub attempts / 15min" value={num(rates.sub_attempts_per_15, 2)} />
          <Stat label="Knockdowns taken / 15" value={num(rates.knockdowns_absorbed_per_15, 2)} />
        </div>
      )}

      {entering && (
        <>
          <h2 className="section-title">Entering their most recent bout</h2>
          <p className="footnote" style={{ marginTop: 0, marginBottom: 14 }}>
            Career totals describe a fighter today. These are the numbers they
            actually walked in with — the view no other source publishes.
          </p>
          <div className="stat-grid">
            <Stat label="Record then" value={`${entering.wins_before}-${entering.bouts_before - entering.wins_before}`}
                  sub={entering.appearances_before !== entering.bouts_before
                        ? `${entering.appearances_before} appearances` : null} />
            <Stat label="Rating then" value={num(entering.rating_before)} />
            <Stat label="Win streak" value={entering.win_streak} />
            <Stat label="Layoff" value={entering.layoff_days !== null ? `${entering.layoff_days}d` : '—'} />
            <Stat label="Avg opponent" value={num(entering.avg_opponent_rating)} />
            <Stat label="Toughest faced" value={num(entering.max_opponent_rating)} />
            <Stat label="KO losses" value={entering.ko_losses} />
            <Stat label="Cage time then" value={`${Math.round((entering.career_seconds || 0) / 60)} min`} />
          </div>
        </>
      )}
    </>
  );
}
