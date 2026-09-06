import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export default function Fighters() {
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    // Debounced so a fast typist does not fire a request per keystroke.
    const timer = setTimeout(() => {
      setLoading(true);
      setTouched(true);
      api.searchFighters(q.trim())
        .then((d) => { if (!cancelled) { setResults(d); setError(null); } })
        .catch((e) => { if (!cancelled) setError(e.message); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [q]);

  return (
    <>
      <div className="page-head">
        <h1>Fighters</h1>
        <p>
          Every fighter with a UFC bout since 1994. Search by name to see their
          rating trajectory, record and career statistics.
        </p>
      </div>

      <div className="controls">
        <input
          type="search"
          value={q}
          autoFocus
          placeholder="Search 2,748 fighters…"
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 300 }}
        />
        {loading && <span className="mono" style={{ fontSize: 12, color: 'var(--faint)' }}>searching…</span>}
      </div>

      {error && <div className="state error">Search failed: {error}</div>}

      {!error && q.trim().length >= 2 && !loading && results.length === 0 && touched && (
        <div className="state">No fighter matches &ldquo;{q}&rdquo;.</div>
      )}

      {q.trim().length < 2 && (
        <div className="state">Type at least two characters to search.</div>
      )}

      {results.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Fighter</th>
                <th>Stance</th>
                <th className="num">Height</th>
                <th className="num">Reach</th>
                <th className="num">Rating</th>
                <th className="num">Last rated</th>
              </tr>
            </thead>
            <tbody>
              {results.map((f) => (
                <tr key={f.id}>
                  <td>
                    <Link to={`/fighters/${f.id}`} className="fighter-name">{f.name}</Link>
                  </td>
                  <td className="division">{f.stance || '—'}</td>
                  <td className="num mono">{f.height ? `${f.height}"` : '—'}</td>
                  <td className="num mono">{f.reach ? `${f.reach}"` : '—'}</td>
                  <td className="num mono">
                    {f.current_rating ? Number(f.current_rating).toFixed(0) : '—'}
                  </td>
                  <td className="num mono">{f.last_rated || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
