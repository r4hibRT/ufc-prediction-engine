import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, useApi } from '../api';

const pct = (v) => `${Math.round(v * 100)}%`;

function fullDate(d) {
  return new Date(`${d}T00:00:00`).toLocaleDateString('en-AU',
    { day: 'numeric', month: 'long', year: 'numeric' });
}

function how(b) {
  if (b.outcome === 'draw') return 'Draw';
  if (b.outcome === 'nc') return 'No contest';
  if (!b.method) return null;
  return b.method === 'Decision' ? 'Decision' : `${b.method} · R${b.round} ${b.time}`;
}

function Name({ f }) {
  return f.id ? <Link to={`/fighters/${f.id}`}>{f.name}</Link> : f.name;
}

function FightRow({ b }) {
  const { a } = b.fighters;
  const winner = b.winner && b.fighters[b.winner];
  const loser = b.winner && b.fighters[b.winner === 'a' ? 'b' : 'a'];
  const mark = b.correct === true ? '✓' : b.correct === false ? '✗' : '–';
  return (
    <li className={`rec-fight${b.correct === false ? ' miss' : ''}`}>
      <span className="rec-mark" aria-label={b.correct ? 'Pick right' : b.correct === false ? 'Pick wrong' : 'No pick'}>
        {mark}
      </span>
      <div className="rec-what">
        <div className="rec-result">
          {winner
            ? <><Name f={winner} /> <span>beat</span> <Name f={loser} /></>
            : <><Name f={a} /> <span>vs</span> <Name f={b.fighters.b} /></>}
        </div>
        <div className="rec-how">{how(b)}</div>
      </div>
      <div className="rec-pick">
        {b.pick
          ? <>Our pick: <b>{b.fighters[b.pick].name}</b> {pct(b.pick_chance)}</>
          : 'Too close to call'}
        {b.upset && <span className="rec-upset">Upset</span>}
      </div>
    </li>
  );
}

function CardBlock({ card, open, onToggle }) {
  return (
    <section className="rec-card">
      <button className="rec-card-head" aria-expanded={open} onClick={onToggle}>
        <span className="rec-card-name">
          <span className="eyebrow">{fullDate(card.date)}</span>
          <span className="rec-card-title">{card.name}</span>
        </span>
        <span className="rec-card-score">
          <b>{card.correct} of {card.picks}</b> picks right
          <span aria-hidden="true">{open ? '▴' : '▾'}</span>
        </span>
      </button>
      {open && (
        <>
          <ul className="rec-fights">{card.bouts.map((b, i) => <FightRow key={i} b={b} />)}</ul>
          <Link className="rec-card-link" to={`/fights/${card.id}`}>See the full card →</Link>
        </>
      )}
    </section>
  );
}

export default function Record() {
  const { data, error, loading } = useApi(() => api.record(), []);
  const [openId, setOpenId] = useState(null);

  if (error) return <div className="state error">{error}</div>;
  if (loading) return <div className="state">Loading the record…</div>;

  const { totals, biggest_upset: upset, cards } = data;
  const shown = openId ?? cards[0]?.id;

  return (
    <>
      <div className="page-head">
        <h1>Record</h1>
        <p>
          Every pick is locked on fight day and checked against the result.
          Nothing is changed afterwards.
        </p>
      </div>

      {!cards.length && (
        <div className="state">No results yet. Picks are checked the Monday after fight night.</div>
      )}

      {cards.length > 0 && (
        <>
          <div className="stat-row rec-stats">
            <div className="stat">
              <span className="stat-label">Picks right</span>
              <b>{totals.correct} / {totals.picks}</b>
              <span className="stat-sub">{pct(totals.correct / totals.picks)} of fights called</span>
            </div>
            <div className="stat">
              <span className="stat-label">Confident picks</span>
              <b>{totals.confident_correct} / {totals.confident_picks}</b>
              <span className="stat-sub">picks we gave {pct(totals.confident_threshold)} or more</span>
            </div>
            {upset && (
              <div className="stat">
                <span className="stat-label">Biggest upset</span>
                <b className="rec-upset-name">{upset.fighters[upset.winner].name}</b>
                <span className="stat-sub">
                  beat {upset.fighters[upset.pick].name} with a {pct(upset.winner_chance)} chance
                </span>
              </div>
            )}
          </div>

          <h2 className="section-title">Card by card</h2>
          <div className="rec-cards">
            {cards.map((card) => (
              <CardBlock key={card.id} card={card} open={shown === card.id}
                         onToggle={() => setOpenId(shown === card.id ? '' : card.id)} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
