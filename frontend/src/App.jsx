import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import Fights from './pages/Fights';
import Rankings from './pages/Rankings';
import Fighters from './pages/Fighters';
import Fighter from './pages/Fighter';
import Placeholder from './pages/Placeholder';
import ThemeToggle from './ThemeToggle';
import HealthBanner from './HealthBanner';

const TABS = [
  { to: '/fights', label: 'Fights' },
  { to: '/record', label: 'Record' },
  { to: '/fighters', label: 'Fighters' },
  { to: '/rankings', label: 'Rankings' },
];

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
              <NavLink
                key={t.to}
                to={t.to}
                className={({ isActive }) => (isActive ? 'active' : undefined)}
              >
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
          <Route
            path="/record"
            element={<Placeholder title="Record" note="How locked forecasts scored once the fights happened. Next." />}
          />
          <Route path="/rankings" element={<Rankings />} />
          <Route path="/fighters" element={<Fighters />} />
          <Route path="/fighters/:id" element={<Fighter />} />
          <Route path="*" element={<Placeholder title="Not found" note="No such page." />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
