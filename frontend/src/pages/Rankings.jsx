import { useMemo, useState } from 'react';
import { api } from '../api';
import { useApi } from '../useApi';

const DIVISIONS = [
  'Flyweight', 'Bantamweight', 'Featherweight', 'Lightweight', 'Welterweight',
  'Middleweight', 'Light Heavyweight', 'Heavyweight',
  "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
  "Women's Featherweight",
];

const MODES = {
  peak: { label: 'All-time peak', blurb: 'Every fighter at their career best, ranked by peak rating less two rating deviations — so a high rating earned over few bouts is discounted against one earned over many.' },
  current: { label: 'Active now', blurb: 'Fighters with a bout inside the last two years, at their current rating.' },
  asof: { label: 'Time machine', blurb: 'The board as it stood on any past date. Every rating at every date is stored, so this is a real historical snapshot rather than a reconstruction.' },
};

/** Rating shown with its uncertainty, because a rating alone overstates
 *  what we know about a fighter with few bouts. */
function RatingBand({ rating, rd, min, max }) {
  const lo = rating - 2 * rd;
  const hi = rating + 2 * rd;
  const span = max - min || 1;
  const left = ((lo - min) / span) * 100;
  const width = ((hi - lo) / span) * 100;
  const dot = ((rating - min) / span) * 100;
  return (
    <div className="band">
      <span className="band-val">{rating.toFixed(0)}</span>
      <div className="band-track" title={`95% interval ${lo.toFixed(0)} – ${hi.toFixed(0)}`}>
        <div className="band-fill" style={{ left: `${left}%`, width: `${width}%` }} />
        <div className="band-dot" style={{ left: `${dot}%` }} />
      </div>
    </div>
  );
}

export default function Rankings() {
  const [mode, setMode] = useState('peak');
  const [division, setDivision] = useState('');
  const [asOf, setAsOf] = useState('2016-06-01');

  const { data, error, loading } = useApi(() => {
    if (mode === 'peak') return api.peakRankings({ limit: 50 });
    if (mode === 'current') return api.currentRankings({ limit: 50, division });
    return api.rankingsAsOf(asOf, { limit: 50, division });
  }, [mode, division, asOf]);

  const rows = useMemo(() => {
    if (!data) return [];
    return data.map((r, i) => ({
      rank: r.rank ?? i + 1,
      id: r.fighter_id ?? r.id,
      name: r.name,
      rating: Number(r.peak_rating ?? r.rating),
      rd: Number(r.rd_at_peak ?? r.rd),
      adjusted: Number(r.adjusted_peak ?? r.adjusted),
      division: r.primary_division ?? r.division ?? '—',
      bouts: r.total_bouts ?? null,
      lastBout: r.last_bout ?? null,
    }));
  }, [data]);

  const [min, max] = useMemo(() => {
    if (!rows.length) return [0, 1];
    const los = rows.map((r) => r.rating - 2 * r.rd);
    const his = rows.map((r) => r.rating + 2 * r.rd);
    return [Math.min(...los), Math.max(...his)];
  }, [rows]);

  return (
    <>
      <div className="page-head">
        <h1>Pound-for-pound rankings</h1>
        <p>{MODES[mode].blurb}</p>
      </div>

      <div className="controls">
        <div className="seg">
          {Object.entries(MODES).map(([key, m]) => (
            <button
              key={key}
              className={mode === key ? 'on' : undefined}
              onClick={() => setMode(key)}
            >
              {m.label}
            </button>
          ))}
        </div>

        {mode === 'asof' && (
          <input
            type="date"
            value={asOf}
            min="1995-01-01"
            max="2026-08-29"
            onChange={(e) => setAsOf(e.target.value)}
          />
        )}

        {mode !== 'peak' && (
          <select value={division} onChange={(e) => setDivision(e.target.value)}>
            <option value="">All divisions</option>
            {DIVISIONS.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        )}
      </div>

      {loading && <div className="state">Loading…</div>}
      {error && <div className="state error">Could not load rankings: {error}</div>}

      {!loading && !error && rows.length === 0 && (
        <div className="state">No fighters match that filter.</div>
      )}

      {!loading && !error && rows.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Fighter</th>
                  <th>Division</th>
                  <th style={{ minWidth: 190 }}>Rating &amp; uncertainty</th>
                  <th className="num">RD</th>
                  <th className="num">Adjusted</th>
                  <th className="num">{mode === 'peak' ? 'Bouts' : 'Last bout'}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.id}-${r.rank}`}>
                    <td className="rank mono">{r.rank}</td>
                    <td>
                      <span className="fighter-name">{r.name}</span>
                    </td>
                    <td className="division">{r.division}</td>
                    <td>
                      <RatingBand rating={r.rating} rd={r.rd} min={min} max={max} />
                    </td>
                    <td className="num mono">{r.rd.toFixed(0)}</td>
                    <td className="num mono">{r.adjusted.toFixed(0)}</td>
                    <td className="num mono">
                      {mode === 'peak' ? (r.bouts ?? '—') : (r.lastBout ?? '—')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="footnote">
            The bar shows each fighter&rsquo;s 95% rating interval. A wide bar means
            few bouts and low confidence, not a worse fighter. Ranking is by the
            conservative end of that interval, which is why a 33-bout veteran can
            outrank someone with a higher raw peak.
          </p>
        </>
      )}
    </>
  );
}
