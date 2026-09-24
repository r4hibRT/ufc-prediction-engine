import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import Fights from './pages/Fights';
import Record from './pages/Record';
import Ratings from './pages/Ratings';
import Fighters from './pages/Fighters';

// The only page with charts, so the charting library loads with it and not before.
const Fighter = lazy(() => import('./pages/Fighter'));

const TABS = [
  { to: '/fights', label: 'Fights' },
  { to: '/record', label: 'Record' },
  { to: '/fighters', label: 'Fighters' },
  { to: '/ratings', label: 'Ratings' },
];

const THEME_KEY = 'ufc-theme';

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
            UFC<em>/</em>FORECAST ENGINE
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

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/fights" replace />} />
          <Route path="/fights" element={<Fights />} />
          <Route path="/fights/:eventId" element={<Fights />} />
          <Route path="/record" element={<Record />} />
          <Route path="/fighters" element={<Fighters />} />
          <Route path="/fighters/:id" element={
            <Suspense fallback={<div className="state">Loading fighter…</div>}><Fighter /></Suspense>
          } />
          <Route path="/ratings" element={<Ratings />} />
          <Route path="/rankings" element={<Navigate to="/ratings" replace />} />
          <Route path="*" element={<Note title="Not found" note="No such page." />} />
        </Routes>
      </main>

      <footer className="site-footer">
        <p>
          Not affiliated with, endorsed by or sponsored by the UFC, Zuffa LLC or TKO
          Group Holdings. UFC is a registered trademark of Zuffa LLC. Forecasts are for
          entertainment only and are not betting advice. Fight data from ufcstats.com.
        </p>
      </footer>
    </BrowserRouter>
  );
}
