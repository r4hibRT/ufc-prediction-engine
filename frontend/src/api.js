import { useEffect, useState } from 'react';

// Built: same origin, so relative paths. Dev: Vite serves the app on 5180
// while the API runs on 8420, so an absolute base is needed.
const BASE = import.meta.env.VITE_API_URL
  ?? (import.meta.env.DEV ? 'http://127.0.0.1:8420' : '');

async function get(path, params) {
  const url = new URL(BASE + '/api' + path, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, v);
  });
  const res = await fetch(url);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  health: () => get('/health'),
  cards: () => get('/cards'),
  card: (id) => get(`/cards/${id}`),
  peakRankings: (p) => get('/rankings', p),
  rankingsAsOf: (date, p) => get(`/rankings/asof/${date}`, p),
  searchFighters: (q) => get('/fighters', { q }),
  fighter: (id) => get(`/fighters/${id}`),
  career: (id) => get(`/fighters/${id}/career`),
  stats: (id) => get(`/fighters/${id}/stats`),
};

/** Fetch on mount and whenever deps change. Handles the three states a
 *  network call actually has, so views never render half-loaded data. */
export function useApi(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading };
}
