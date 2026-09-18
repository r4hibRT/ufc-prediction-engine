// Built: same origin, so relative paths. Dev: Vite serves the app on 5180
// while the API runs on 8420, so an absolute base is needed.
const BASE = import.meta.env.VITE_API_URL
  ?? (import.meta.env.DEV ? 'http://127.0.0.1:8420' : '');

export async function get(path, params) {
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
  peakRankings: (p) => get('/rankings', p),
  rankingsAsOf: (date, p) => get(`/rankings/asof/${date}`, p),
  searchFighters: (q) => get('/fighters', { q }),
  fighter: (id) => get(`/fighters/${id}`),
  career: (id) => get(`/fighters/${id}/career`),
  stats: (id) => get(`/fighters/${id}/stats`),
  movers: (p) => get('/movers', p),
  ratingsCard: () => get('/ratings-card'),
};
