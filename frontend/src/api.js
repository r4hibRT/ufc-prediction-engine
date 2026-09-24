import { useEffect, useState } from 'react';

// Built: same origin, so relative paths. Dev: Vite serves the app on 5180
// while the API runs on 8420, so an absolute base is needed.
const BASE = import.meta.env.VITE_API_URL
  ?? (import.meta.env.DEV ? 'http://127.0.0.1:8420' : '');

// The published site ("vite build --mode static") has no server: every response
// is a pre-built JSON file under /data, written by src/publish.py.
const STATIC = import.meta.env.MODE === 'static';

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

async function file(path) {
  const res = await fetch(`/data${path}.json`);
  // A missing file comes back as the app's own page, not a 404, so check the type.
  if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) {
    throw new Error('Not found');
  }
  return res.json();
}

/** Same name as src/publish.py gives each division's ratings file. */
const slug = (division) => division.toLowerCase().replace(/[^a-z0-9]+/g, '-');

let fighterIndex;
async function searchIndex(q) {
  fighterIndex ??= file('/fighters');
  const needle = q.toLowerCase();
  return (await fighterIndex).filter((f) => f.name.toLowerCase().includes(needle)).slice(0, 20);
}

export const api = STATIC ? {
  meta: () => file('/meta'),
  cards: () => file('/cards'),
  card: (id) => file(`/cards/${id}`),
  record: () => file('/record'),
  ratings: ({ division } = {}) => file(division ? `/rankings/${slug(division)}` : '/rankings'),
  searchFighters: searchIndex,
  fighter: (id) => file(`/fighters/${id}`),
  career: (id) => file(`/fighters/${id}/career`),
  stats: (id) => file(`/fighters/${id}/stats`),
} : {
  meta: () => get('/meta'),
  cards: () => get('/cards'),
  card: (id) => get(`/cards/${id}`),
  record: () => get('/record'),
  ratings: (p) => get('/rankings', p),
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
