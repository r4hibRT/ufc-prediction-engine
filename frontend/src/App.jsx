import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { api } from './api';
import Fights from './pages/Fights';
import Rankings from './pages/Rankings';
import Fighters from './pages/Fighters';
import Fighter from './pages/Fighter';

const TABS = [
  { to: '/fights', label: 'Fights' },
  { to: '/record', label: 'Record' },
  { to: '/fighters', label: 'Fighters' },
  { to: '/rankings', label: 'Rankings' },
];

const THEME_KEY = 'ufc-theme';

// Plain-language versions of the pipeline checks; detail comes from the API.
const HEALTH_MESSAGES = {
  last_run_succeeded: (d) => `The last scheduled refresh failed (${d}).`,
  last_run_recent: (d) => `The scheduled refresh last ran ${d}.`,
  data_fresh: (d) => `Data may be out of date: ${d}.`,
  snapshots_consistent: () => 'Fighter statistics are out of sync with bout records.',
  no_stuck_events: (d) => `Some events could not be fully scraped: ${d}.`,
};

/** Dark by default; the choice is remembered and overrides the OS setting. */
function ThemeToggle() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(THEME_KEY) || 'dark';
    } catch {
      return 'dark';
    }
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* private browsing: the choice just will not persist */
    }
  }, [theme]);

  return (
    <button
      className="theme-toggle"
      onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
    >
      {theme === 'dark' ? 'Light' : 'Dark'}
    </button>
  );
}

/** Shown only when the pipeline is unhealthy, so stale data is never silent. */
function HealthBanner() {
  const [problems, setProblems] = useState([]);

  useEffect(() => {
    api.health()
      .then((h) => {
        const failing = (h.pipeline?.checks || []).filter((c) => !c.ok);
        setProblems(failing.map((c) =>
          (HEALTH_MESSAGES[c.name] || ((d) => `${c.name}: ${d}`))(c.detail)));
      })
      .catch(() => setProblems(['Could not reach the data service.']));
  }, []);

  if (!problems.length) return null;
  return (
    <div className="health-banner" role="status">
      <div className="health-inner">
        <span className="health-label">Data warning</span>
        <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      </div>
    </div>
  );
}

function Note({ title, note }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      <p>{note}</p>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <header className="masthead">
        <div className="masthead-inner">
          <div className="wordmark">
            UFC<em>/</em>RATINGS <span>1994&ndash;2026</span>
          </div>
          <nav className="tabs">
            {TABS.map((t) => (
              <NavLink key={t.to} to={t.to}
                       className={({ isActive }) => (isActive ? 'active' : undefined)}>
                {t.label}
              </NavLink>
            ))}
          </nav>
          <ThemeToggle />
        </div>
      </header>
      <HealthBanner />

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/fights" replace />} />
          <Route path="/fights" element={<Fights />} />
          <Route path="/fights/:eventId" element={<Fights />} />
          <Route path="/record"
                 element={<Note title="Record" note="How locked forecasts scored once the fights happened. Coming next." />} />
          <Route path="/fighters" element={<Fighters />} />
          <Route path="/fighters/:id" element={<Fighter />} />
          <Route path="/rankings" element={<Rankings />} />
          <Route path="*" element={<Note title="Not found" note="No such page." />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
