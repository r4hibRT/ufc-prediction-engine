import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useApi } from '../useApi';

const DIVISIONS = [
  'Flyweight', 'Bantamweight', 'Featherweight', 'Lightweight', 'Welterweight',
  'Middleweight', 'Light Heavyweight', 'Heavyweight',
  "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
  "Women's Featherweight",
];

const MODES = {
  peak: {
    label: 'All-time',
    blurb: 'Every fighter at their career best, ranked by peak rating less two rating deviations, so a high rating earned over few bouts is discounted against one earned over many.',
  },
  asof: {
    label: 'Time machine',
    blurb: 'The board as it stood on any past date. Every rating at every date is stored, so this is a real historical snapshot rather than a reconstruction.',
  },
};

/** Rating with its uncertainty, because a rating alone overstates what is
 *  known about a fighter with few bouts. */
function RatingBand({ rating, rd, min, max }) {
  const lo = rating - 2 * rd;
  const hi = rating + 2 * rd;
  const span = max - min || 1;
  return (
    <div className="band">
      <span className="band-val">{rating.toFixed(0)}</span>
      <div className="band-track" title={`95% interval ${lo.toFixed(0)} – ${hi.toFixed(0)}`}>
        <div className="band-fill" style={{ left: `${((lo - min) / span) * 100}%`,
                                            width: `${((hi - lo) / span) * 100}%` }} />
        <div className="band-dot" style={{ left: `${((rating - min) / span) * 100}%` }} />
      </div>
    </div>
  );
}

export default function Rankings() {
  const [mode, setMode] = useState('peak');
  const [division, setDivision] = useState('');
  const [asOf, setAsOf] = useState('2016-06-01');

  const { data, error, loading } = useApi(
    () => (mode === 'peak'
      ? api.peakRankings({ limit: 50 })
      : api.rankingsAsOf(asOf, { limit: 50, division })),
    [mode, division, asOf],
  );

  const rows = useMemo(() => (data || []).map((r, i) => ({
    rank: r.rank ?? i + 1,
    id: r.id,
    name: r.name,
    division: r.division || '—',
    rating: Number(r.peak_rating ?? r.rating),
    rd: Number(r.rd),
    record: r.wins === undefined ? null
      : `${r.wins}-${r.losses}${r.draws ? `-${r.draws}` : ''}`,
    titleWins: r.title_wins,
    defences: r.title_defences,
    divisions: r.title_divisions,
    bestWin: r.best_win_name,
    bestWinRating: r.best_win_rating,
    streak: r.best_streak,
    lastBout: r.last_bout,
  })), [data]);

  const [min, max] = useMemo(() => {
    if (!rows.length) return [0, 1];
    return [
      Math.min(...rows.map((r) => r.rating - 2 * r.rd)),
      Math.max(...rows.map((r) => r.rating + 2 * r.rd)),
    ];
  }, [rows]);

  const full = mode === 'peak';

  return (
    <>
      <div className="page-head">
        <h1>Pound-for-pound</h1>
        <p>{MODES[mode].blurb}</p>
      </div>

      <div className="controls">
        <div className="seg">
          {Object.entries(MODES).map(([key, m]) => (
            <button key={key} className={mode === key ? 'on' : undefined}
                    onClick={() => setMode(key)}>
              {m.label}
            </button>
          ))}
        </div>

        {mode === 'asof' && (
          <>
            <input type="date" value={asOf} min="1995-01-01" max="2026-08-29"
                   onChange={(e) => setAsOf(e.target.value)} />
            <select value={division} onChange={(e) => setDivision(e.target.value)}>
              <option value="">All divisions</option>
              {DIVISIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </>
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
                  {full && <th className="num">Record</th>}
                  {full && <th className="num">Title wins</th>}
                  {full && <th>Best win</th>}
                  {full && <th className="num">Best run</th>}
                  <th style={{ minWidth: 186 }}>Rating</th>
                  {!full && <th className="num">Last bout</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.id}-${r.rank}`}>
                    <td className={`rank${r.rank <= 3 ? ' top' : ''}`}>{r.rank}</td>
                    <td>
                      <Link to={`/fighters/${r.id}`} className="fighter-name">{r.name}</Link>
                      {r.divisions >= 2 && (
                        <span className="champ" title="Won a title in more than one division">
                          {r.divisions}-div champ
                        </span>
                      )}
                    </td>
                    <td className="division">{r.division}</td>

                    {full && <td className="num">{r.record ?? '—'}</td>}
                    {full && (
                      <td className="num titles">
                        {r.titleWins || '—'}
                        {r.defences > 0 && <span> ({r.defences} def)</span>}
                      </td>
                    )}
                    {full && (
                      <td className="best-win">
                        {r.bestWin
                          ? <><b>{r.bestWin}</b> <span>{r.bestWinRating}</span></>
                          : '—'}
                      </td>
                    )}
                    {full && <td className="num">{r.streak || '—'}</td>}

                    <td><RatingBand rating={r.rating} rd={r.rd} min={min} max={max} /></td>
                    {!full && <td className="num">{r.lastBout ?? '—'}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="footnote">
            <b>Title wins</b> counts championship bouts won, with successful
            defences in brackets; tournament finals from Road to UFC and the
            Contender Series are excluded. <b>Best win</b> is the highest-rated
            opponent a fighter has beaten, at the rating that opponent held going
            into the fight. <b>Best run</b> is their longest winning streak. The
            bar shows the 95% rating interval — a wide bar means few bouts and
            low confidence, not a worse fighter.
          </p>
        </>
      )}
    </>
  );
}
