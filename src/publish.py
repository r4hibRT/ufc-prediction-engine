"""Publishing the site: every page's data as static files, uploaded to Cloudflare Pages.

The site is read-only and its data changes only when the refresh runs, so the
public copy needs no server and no hosted database. After each refresh this
module builds the frontend in its "static" mode, then fetches every API route
the pages use through the app itself, so each file is byte-for-byte what the API
would serve, and writes it under frontend/dist/data. Fighter search becomes a
name index filtered in the browser. The folder is then uploaded with Cloudflare's
wrangler CLI, which only sends files that changed since the last upload.

Configured in .env: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (without them
the upload is skipped), CLOUDFLARE_PROJECT (default ufc-forecast-engine), and
SITE_URL, the public address that link previews need.

Run:
    python -m src.publish              # build, export and upload
    python -m src.publish --no-upload  # build and export only, to inspect locally
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from src.db import NON_DIVISIONS

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
DEFAULT_PROJECT = "ufc-forecast-engine"
WRANGLER = "wrangler@4"


def slug(division):
    """File name for a division's ratings; the frontend builds the same one."""
    return re.sub(r"[^a-z0-9]+", "-", division.lower())


def configured():
    return bool(os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN"))


def _run(command, cwd, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command[:3])} failed:\n{(result.stderr or result.stdout)[-1500:]}")
    return result.stdout


def build_frontend():
    """Production build in static mode, where the pages read files, not the API."""
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found on PATH")
    _run([npm, "run", "build", "--", "--mode", "static"], FRONTEND)


def _divisions():
    from src.api.queries import _rows
    rows = _rows("SELECT DISTINCT weight_class FROM bouts WHERE weight_class IS NOT NULL")
    return sorted(r["weight_class"] for r in rows if r["weight_class"] not in NON_DIVISIONS)


def _fighter_ids():
    from src.api.queries import _rows
    return [r["id"] for r in _rows("SELECT id FROM fighters ORDER BY id")]


def export(out, fighter_ids=None, log=print):
    """Write every route the pages read to `out`, one JSON file per response."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.api.queries import search_fighters

    client = TestClient(app)
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    written = 0

    def save(route, path, params=None):
        nonlocal written
        response = client.get("/api" + route, params=params)
        if response.status_code == 404:
            return None  # e.g. a fighter with no rated bouts has no career
        response.raise_for_status()
        target = out / f"{path}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        written += 1
        return response

    save("/meta", "meta")
    save("/record", "record")
    for card in save("/cards", "cards").json():
        save(f"/cards/{card['id']}", f"cards/{card['id']}")
    save("/rankings", "rankings")
    for division in _divisions():
        save("/rankings", f"rankings/{slug(division)}", params={"division": division})

    ids = _fighter_ids() if fighter_ids is None else fighter_ids
    for fighter_id in ids:
        for part in ("", "/career", "/stats"):
            save(f"/fighters/{fighter_id}{part}", f"fighters/{fighter_id}{part}")

    # Search runs in the browser over every fighter, already in the API's order.
    index = jsonable_encoder(search_fighters("", limit=None))
    (out / "fighters.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                                       encoding="utf-8")
    log(f"Exported {written + 1} data files for {len(ids)} fighters.")
    return written + 1


def point_previews_at(site_url):
    """Link previews need an absolute image address, known only once the site is live."""
    page = DIST / "index.html"
    html = page.read_text(encoding="utf-8")
    page.write_text(html.replace('content="/og.png"', f'content="{site_url.rstrip("/")}/og.png"'),
                    encoding="utf-8")


def upload(log=print):
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("npx not found on PATH")
    project = os.getenv("CLOUDFLARE_PROJECT") or DEFAULT_PROJECT
    output = _run([npx, "--yes", WRANGLER, "pages", "deploy", str(DIST), "--project-name", project,
                   "--branch", "main", "--commit-dirty=true"], ROOT, env=os.environ.copy())
    address = next((w for w in output.split() if w.startswith("https://")), None)
    log(f"Uploaded to Cloudflare Pages ({project})" + (f": {address}" if address else "."))
    return address


def run(dry_run=False, log=print, send=True):
    if dry_run:
        return {"skipped": "dry-run"}
    if send and not configured():
        log("No Cloudflare credentials in .env; skipping publish.")
        return {"skipped": "not configured"}
    build_frontend()
    files = export(DIST / "data", log=log)
    if os.getenv("SITE_URL"):
        point_previews_at(os.getenv("SITE_URL"))
    return {"files": files, "address": upload(log) if send else None}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build, export and upload the static site.")
    parser.add_argument("--no-upload", action="store_true", help="build and export only")
    run(send=not parser.parse_args().no_upload)
