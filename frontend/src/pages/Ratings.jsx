import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, useApi } from '../api';

const DIVISIONS = [
  'Flyweight', 'Bantamweight', 'Featherweight', 'Lightweight', 'Welterweight',
  'Middleweight', 'Light Heavyweight', 'Heavyweight',
  "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
  "Women's Featherweight",
];

/** Rating with its uncertainty, because a rating alone overstates what is
 *  known about a fighter with few bouts. */
function RatingBand({ rating, rd, min, max }) {
  const lo = rating - 2 * rd;
  const hi = rating + 2 * rd;
  const span = max - min || 1;
  return (
    <div className="band">
      <span className="band-val">{rating.toFixed(0)}</span>
      <div className="band-track" title={`Likely range ${lo.toFixed(0)} – ${hi.toFixed(0)}`}>
        <div className="band-fill" style={{ left: `${((lo - min) / span) * 100}%`,
                                            width: `${((hi - lo) / span) * 100}%` }} />
        <div className="band-dot" style={{ left: `${((rating - min) / span) * 100}%` }} />
      </div>
    </div>
  );
}

function HowItWorks() {
  return (
    <ul className="explainer">
      <li>
        <b>Elo system</b> Built on Glicko-2, a refinement of the Elo ratings used
        in chess. Win and a fighter&rsquo;s rating goes up, lose and it goes down;
        beating a top-rated fighter is worth far more than beating a newcomer.
      </li>
      <li>
        <b>Proven records rank first</b> A rating built on only a few fights is
        less certain, which shows as a wider bar beside it. Fighters are ranked by
        the cautious end of that bar, so years of winning count for more than a
        short hot streak.
      </li>
      <li>
        <b>Model limitations</b> It sees results, not context: styles,
        short-notice bookings, injuries and weight cuts are invisible to it. Every
        win counts the same, whether a knockout, a split decision or a
        disqualification; draws count as half a win and no contests not at all.
        Only UFC fights count, so every debutant starts level.
      </li>
    </ul>
  );
}

export default function Ratings() {
  const [division, setDivision] = useState('');
  const { data, error, loading } = useApi(
    () => api.ratings({ limit: 50, division }),
    [division],
  );

  const rows = useMemo(() => (data || []).map((r) => ({
    rank: r.rank,
    id: r.id,
    name: r.name,
    division: r.division || '—',
    rating: Number(r.peak_rating),
    rd: Number(r.rd),
    record: `${r.wins}-${r.losses}${r.draws ? `-${r.draws}` : ''}`,
    titleWins: r.title_wins,
    divisions: r.title_divisions,
    bestWin: r.best_win_name,
    bestWinRating: r.best_win_rating,
    streak: r.best_streak,
    bouts: r.bouts,
  })), [data]);

  const [min, max] = useMemo(() => {
    if (!rows.length) return [0, 1];
    return [
      Math.min(...rows.map((r) => r.rating - 2 * r.rd)),
      Math.max(...rows.map((r) => r.rating + 2 * r.rd)),
    ];
  }, [rows]);

  const all = !division;

  return (
    <>
      <div className="page-head">
        <h1>All-time ratings</h1>
        <p>
          {all
            ? 'Every fighter at their career best, rated on results against the opposition they actually faced.'
            : `Each fighter at their best at ${division.toLowerCase()}, judged only on the fights they had there. A great who passed through for a fight or two does not outrank the division's long-reigning names.`}
        </p>
      </div>

      <HowItWorks />

      <div className="controls">
        <select value={division} onChange={(e) => setDivision(e.target.value)}
                aria-label="Division">
          <option value="">All divisions</option>
          {DIVISIONS.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      {loading && <div className="state">Loading…</div>}
      {error && <div className="state error">Could not load ratings: {error}</div>}
      {!loading && !error && rows.length === 0 && (
        <div className="state">No fighters in this division have enough fights yet.</div>
      )}

      {!loading && !error && rows.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Fighter</th>
                  {all && <th>Division</th>}
                  <th className="num">{all ? 'Record' : 'Record here'}</th>
                  <th className="num">Title wins</th>
                  {all && <th>Best win</th>}
                  {all && <th className="num">Best run</th>}
                  {!all && <th className="num">Fights here</th>}
                  <th style={{ minWidth: 186 }}>Rating</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className={`rank${r.rank <= 3 ? ' top' : ''}`}>{r.rank}</td>
                    <td>
                      <Link to={`/fighters/${r.id}`} className="fighter-name">{r.name}</Link>
                      {all && r.divisions >= 2 && (
                        <span className="champ" title="Won a title in more than one division">
                          {r.divisions}-div champ
                        </span>
                      )}
                    </td>
                    {all && <td className="division">{r.division}</td>}
                    <td className="num">{r.record}</td>
                    <td className="num titles">{r.titleWins || '—'}</td>
                    {all && (
                      <td className="best-win">
                        {r.bestWin
                          ? <><b>{r.bestWin}</b> <span>{r.bestWinRating}</span></>
                          : '—'}
                      </td>
                    )}
                    {all && <td className="num">{r.streak || '—'}</td>}
                    {!all && <td className="num">{r.bouts}</td>}
                    <td><RatingBand rating={r.rating} rd={r.rd} min={min} max={max} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="footnote">
            <b>Title wins</b> counts championship bouts won; tournament finals
            such as The Ultimate Fighter and Road to UFC are excluded.
            {all
              ? <> <b>Best win</b> is the highest-rated opponent a fighter has beaten,
                  at the rating that opponent held going into the fight. <b>Best run</b> is
                  their longest winning streak.</>
              : <> Division lists use a separate rating built only from fights in
                  that division, and need at least three fights there.</>}
          </p>
        </>
      )}
    </>
  );
}
