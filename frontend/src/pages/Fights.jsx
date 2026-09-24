import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, useApi } from '../api';

const STATUS = {
  upcoming: 'Forecasts can update until fight day',
  locked: 'Forecasts locked for fight night',
  scored: 'Results in',
};

const dec2 = (v) => v.toFixed(2);
const pct = (v) => `${Math.round(v * 100)}%`;
const feet = (v) => `${Math.floor(v / 12)}'${Math.round(v % 12)}"`;

function since(days) {
  if (days == null) return null;
  return days < 60 ? `${days} days` : `${Math.round(days / 30.4)} months`;
}

// Tale-of-the-tape rows: the few that shape a fight, not the full stat sheet.
// `better` marks which way a stat favours a fighter.
const TAPE = [
  { label: 'UFC record', value: (f) => f.record && `${f.record.w}-${f.record.l}-${f.record.d}` },
  { label: 'Glicko rating', value: (f) => f.rating, better: 'high' },
  { label: 'Age', value: (f) => f.age && Math.floor(f.age) },
  { label: 'Height', value: (f) => f.height, fmt: feet },
  { label: 'Reach', value: (f) => f.reach, fmt: (v) => `${v}"` },
  { label: 'Stance', value: (f) => f.stance },
  { label: 'Strikes landed / min', value: (f) => f.stats?.slpm, fmt: dec2, better: 'high' },
  { label: 'Strikes absorbed / min', value: (f) => f.stats?.sapm, fmt: dec2, better: 'low' },
  { label: 'Takedown defence', value: (f) => f.stats?.td_def, fmt: pct, better: 'high' },
  { label: 'Wins KO · Sub · Dec', value: (f) => f.wins_by && `${f.wins_by.ko} · ${f.wins_by.sub} · ${f.wins_by.dec}` },
  { label: 'Since last fight', value: (f) => since(f.days_since_last) },
];

function fullDate(d, opts = { weekday: 'long', day: 'numeric', month: 'long' }) {
  return new Date(`${d}T00:00:00`).toLocaleDateString('en-AU', opts);
}

/** "UFC 331: Van vs. Pantoja 2" -> "UFC 331"; Fight Nights keep their headline. */
function shortName(name) {
  const [head, rest] = name.split(': ');
  return /^UFC \d/.test(head) || !rest ? head : rest;
}

function billing(position) {
  return position === 1 ? 'Main event' : position === 2 ? 'Co-main event' : null;
}

function FighterName({ f }) {
  return f.id
    ? <Link className="fighter-name" to={`/fighters/${f.id}`}>{f.name}</Link>
    : <span className="fighter-name">{f.name}</span>;
}

function Corner({ f, side, result }) {
  const bits = f.debut
    ? ['UFC debut', f.age && `${Math.floor(f.age)} yrs`]
    : [f.record && `${f.record.w}-${f.record.l}-${f.record.d}`, f.streak,
       f.age && `${Math.floor(f.age)} yrs`];
  return (
    <div className={`corner corner-${side}`}>
      <FighterName f={f} />
      <div className="corner-sub">
        {result?.winner === side && <span className="res res-win">Win</span>}
        {bits.filter(Boolean).join(' · ')}
      </div>
    </div>
  );
}

/** The two win probabilities as one split bar, favourite in red. */
function Odds({ pa }) {
  const aFav = pa >= 0.5;
  return (
    <div className="odds" role="img"
         aria-label={`${Math.round(pa * 100)}% to ${Math.round((1 - pa) * 100)}%`}>
      <span className={`odds-pct num${aFav ? ' fav' : ''}`}>{Math.round(pa * 100)}%</span>
      <div className="odds-bar">
        <div className={`odds-seg${aFav ? ' fav' : ''}`} style={{ width: `${pa * 100}%` }} />
        <div className={`odds-seg${aFav ? '' : ' fav'}`} style={{ width: `${(1 - pa) * 100}%` }} />
        <div className="odds-mid" />
      </div>
      <span className={`odds-pct num${aFav ? '' : ' fav'}`}>{Math.round((1 - pa) * 100)}%</span>
    </div>
  );
}

function Tape({ a, b, narrative }) {
  return (
    <div className="tape">
      {TAPE.map((row) => {
        const va = row.value(a);
        const vb = row.value(b);
        if (va == null && vb == null) return null;
        const show = (v) => (v == null ? '—' : row.fmt ? row.fmt(v) : v);
        let edge = null;
        if (row.better && va != null && vb != null && va !== vb) {
          edge = (va > vb) === (row.better === 'high') ? 'a' : 'b';
        }
        return (
          <div className="tape-row" key={row.label}>
            <span className={`tape-val num${edge === 'a' ? ' edge' : ''}`}>{show(va)}</span>
            <span className="tape-label">{row.label}</span>
            <span className={`tape-val num${edge === 'b' ? ' edge' : ''}`}>{show(vb)}</span>
          </div>
        );
      })}
      {narrative && (
        <section className="insights">
          <h3 className="insights-title">Model insights</h3>
          <p>{narrative}</p>
        </section>
      )}
    </div>
  );
}

function ResultLine({ result }) {
  if (!result) return null;
  if (!result.winner) {
    return <div className="bout-result">{result.outcome === 'draw' ? 'Draw' : 'No contest'}</div>;
  }
  const how = [result.method, result.round && `R${result.round}`, result.time].filter(Boolean).join(' · ');
  // Same verdicts as the Record page; the server applies one rule to both.
  const [label, tone] = result.upset ? ['Upset', 'pick-miss']
    : result.correct ? ['Pick right', 'pick-hit']
    : result.correct === false ? ['Pick wrong', 'pick-wrong']
    : ['Too close to call', 'pick-wrong'];
  return (
    <div className="bout-result">
      <span>{how}</span>
      <span className={`pick ${tone}`}>{label}</span>
    </div>
  );
}

function FightCard({ bout }) {
  const [open, setOpen] = useState(false);
  const { a, b } = bout.fighters;
  return (
    <article className={`bout${bout.position === 1 ? ' bout-main' : ''}`}>
      <div className="bout-names">
        <Corner f={a} side="a" result={bout.result} />
        <div className="bout-mid">
          {billing(bout.position) && <span className="eyebrow">{billing(bout.position)}</span>}
          <span className="bout-division">{bout.weight_class}</span>
        </div>
        <Corner f={b} side="b" result={bout.result} />
      </div>
      <Odds pa={bout.prediction.p_a} />
      <ResultLine result={bout.result} />
      <button className="tape-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        Tale of the tape <span aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>
      {open && <Tape a={a} b={b} narrative={bout.narrative} />}
    </article>
  );
}

/** Nearest card still to be settled, else the most recent one. */
function defaultCard(cards) {
  if (!cards?.length) return null;
  return (cards.find((c) => c.status !== 'scored') || cards[cards.length - 1]).id;
}

export default function Fights() {
  const { eventId } = useParams();
  const cards = useApi(() => api.cards(), []);
  const selected = eventId || defaultCard(cards.data);
  const card = useApi(() => (selected ? api.card(selected) : Promise.resolve(null)), [selected]);

  if (cards.error) return <div className="state error">{cards.error}</div>;
  if (cards.loading) return <div className="state">Loading cards…</div>;
  if (!cards.data.length) return <div className="state">No upcoming cards listed yet.</div>;

  const ev = card.data?.event;
  return (
    <>
      <nav className="card-strip" aria-label="Cards">
        {cards.data.map((c) => (
          <Link key={c.id} to={`/fights/${c.id}`}
                className={c.id === selected ? 'active' : undefined}>
            <span className="strip-date">{fullDate(c.date, { day: 'numeric', month: 'short' })}</span>
            <span className="strip-name">{shortName(c.name)}</span>
          </Link>
        ))}
      </nav>

      {card.error && <div className="state error">{card.error}</div>}
      {card.loading && <div className="state">Loading card…</div>}
      {ev && !card.loading && (
        <>
          <header className="page-head">
            <div className="eyebrow">
              {fullDate(ev.date)} · {card.data.bouts.length} fights
            </div>
            <h1>{ev.name}</h1>
            <p><span className={`status status-${ev.status}`}>{STATUS[ev.status]}</span></p>
          </header>
          <div className="bouts">
            {card.data.bouts.map((bout) => <FightCard key={bout.id} bout={bout} />)}
          </div>
        </>
      )}
    </>
  );
}
