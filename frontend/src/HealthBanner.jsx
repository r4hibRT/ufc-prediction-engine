import { useEffect, useState } from 'react';
import { api } from './api';

// Plain-language versions of the pipeline checks; detail comes from the API.
const MESSAGES = {
  last_run_succeeded: (d) => `The last weekly refresh failed (${d}).`,
  last_run_recent: (d) => `The weekly refresh last ran ${d}.`,
  data_fresh: (d) => `Data may be out of date: ${d}.`,
  snapshots_consistent: () => 'Fighter statistics are out of sync with bout records.',
  no_stuck_events: (d) => `Some events could not be fully scraped: ${d}.`,
};

/** Shown only when the pipeline is unhealthy, so stale data is never silent. */
export default function HealthBanner() {
  const [problems, setProblems] = useState([]);

  useEffect(() => {
    api.health()
      .then((h) => {
        const failing = (h.pipeline?.checks || []).filter((c) => !c.ok);
        setProblems(failing.map((c) =>
          (MESSAGES[c.name] || ((d) => `${c.name}: ${d}`))(c.detail)));
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
