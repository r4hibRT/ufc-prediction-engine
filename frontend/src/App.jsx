import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import Rankings from './pages/Rankings';
import Fighters from './pages/Fighters';
import Fighter from './pages/Fighter';
import Placeholder from './pages/Placeholder';

const TABS = [
  { to: '/rankings', label: 'Rankings' },
  { to: '/fighters', label: 'Fighters' },
  { to: '/compare', label: 'Compare' },
  { to: '/divisions', label: 'Divisions' },
  { to: '/insights', label: 'Insights' },
  { to: '/ratings-card', label: 'The Ratings' },
];

export default function App() {
  return (
    <BrowserRouter>
      <header className="masthead">
        <div className="masthead-inner">
          <div className="wordmark">
            UFC Ratings <span>1994&ndash;2026</span>
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
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/rankings" replace />} />
          <Route path="/rankings" element={<Rankings />} />
          <Route path="/fighters" element={<Fighters />} />
          <Route path="/fighters/:id" element={<Fighter />} />
          <Route
            path="/compare"
            element={<Placeholder title="Compare" note="Two trajectories, tale of the tape, common opponents. Step 7." />}
          />
          <Route
            path="/divisions"
            element={<Placeholder title="Divisions" note="Champions, title lineage and division trends. Step 7." />}
          />
          <Route
            path="/insights"
            element={<Placeholder title="Insights" note="Era inflation, upsets, records and movers. Step 9." />}
          />
          <Route
            path="/ratings-card"
            element={<Placeholder title="The Ratings" note="How the Glicko-2 engine works and where it stops working. Step 10." />}
          />
          <Route path="*" element={<Placeholder title="Not found" note="No such page." />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
