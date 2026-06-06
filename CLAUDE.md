# CLAUDE.md

High-level index for this repository. Deep detail lives in `.claude/docs/` —
follow the links in section 6 instead of duplicating it here.

## 1. Project Overview
Local web app for analysing lithium-ion battery cell test data. It loads
BatteryML `.pkl` cycling datasets and renders interactive charts (dQ/dV, dV/dQ,
capacity fade, degradation features), and it has a separate **ECM** tab that
extracts HPPC pulses from Neware `.xlsx` exports and fits an equivalent-circuit
model (R0, R1, C1 … per SOC). Everything runs on one machine; there is no
database and no cloud component.

## 2. Tech Stack
- Python 3.12 (works on 3.11+).
- Backend: FastAPI ≥0.110, Uvicorn ≥0.27, Pydantic ≥2.0.
- Compute: NumPy ≥1.24, Pandas, SciPy, Matplotlib ≥3.7, openpyxl.
- Frontend: vanilla JS + HTML/CSS, Plotly 2.35 (CDN). **No framework, no build step.**
- ECM engine: standalone `equiv-circ-model/` package (imported at runtime).

## 3. Dev Commands
Install:
```bash
pip install -r requirements.txt            # web app
pip install -r equiv-circ-model/requirements.txt   # ECM engine (scipy/openpyxl/pandas)
```
Run the dev server (serves API + UI on http://127.0.0.1:8765):
```bash
./run_webapp.sh                            # macOS/Linux (sets PYTHONPATH)
run_webapp_windows.cmd                     # Windows
# equivalent: PYTHONPATH=. python3 -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
```
Build: **none** — the frontend is static files under `webapp/UI/`, served directly.
PYTHONPATH must include the project root so `import webapp` and `import ecm` resolve.

## 4. Core Logic Summary
There is **no weighting/scoring model** in this app — do not invent one. The two
core computations are:
- **Feature analysis** (`webapp/plot/charts.py`): per-cycle curves (dQ/dV, dV/dQ,
  Q-vs-V) and degradation features such as `log⟨|Δ metric|⟩ vs cycle`, computed as
  the cycle's absolute difference from a chosen reference cycle, averaged, then
  `log10`. Capacity fade and end-of-life (80% threshold) come from the Qd series.
- **ECM fitting** (`webapp/data_processing/ecm_runner.py` → `equiv-circ-model/`):
  detect cell capacity (Qd from the HPPC sweep, Qc from the final CCCV charge),
  extract HPPC pulses, then curve-fit R/C/τ per SOC for a 1RC or 2RC model.
Details: [plotting-and-features.md](.claude/docs/plotting-and-features.md),
[ecm-pipeline.md](.claude/docs/ecm-pipeline.md).

## 5. Key Constraints
- **One task per file/folder.** Keep `api/routes.py` thin (HTTP only) and put real
  logic in `data_processing/` or `plot/`. Do not pile unrelated logic into one module.
- **Never hardcode dataset field names.** Resolve voltage/current/Qd/etc. through
  the alias tuples in `data_processing/inspection.py` (datasets differ).
- **Bump `CACHE_VERSION` in `webapp/config.py`** whenever you change the cached
  `row` shape, or stale caches will be read. Cache invalidates per file by `(size, mtime_ns)`.
- **Sessions are in-memory and volatile** — assume they vanish on restart; a missing
  session is a 404 the frontend recovers from by reloading.
- **Keep the path jails.** User paths go through `data_processing/paths.py`; the ECM
  image endpoint must only serve files under `equiv-circ-model/Equivalent-Circuit/`.
- **ECM capacity is auto-detected, not assumed** (the old 30.6 Ah CLI default is wrong
  for other cells). ECM input is assumed to be the fixed Neware `.xlsx` format.
- Do not add a frontend framework or build step; do not introduce a database.
- Generated outputs (`webapp/cache/`, `equiv-circ-model/Equivalent-Circuit/`) are gitignored — never commit them.
- **Branch Manegements on Git**: Before adding any features or fix bugs, always work on new git branch. Never comit directly on main. Bug branches must follow naming convention bug/[des], feature branches follow naming convention feature/[desc]. After get confirmation from developer, push complete version to main branch (always ask developer if they accept the version).

## 6. Additional Documentation
- [.claude/docs/architecture.md](.claude/docs/architecture.md) — system shape, module map, request flow, how to extend.
- [.claude/docs/state-and-caching.md](.claude/docs/state-and-caching.md) — sessions, folder cache schema, frontend state, path safety.
- [.claude/docs/data-model.md](.claude/docs/data-model.md) — BatteryML `.pkl` structure, field aliases, ECM CSV formats.
- [.claude/docs/api-reference.md](.claude/docs/api-reference.md) — every `/api/*` endpoint.
- [.claude/docs/plotting-and-features.md](.claude/docs/plotting-and-features.md) — chart dispatch and metric math.
- [.claude/docs/ecm-pipeline.md](.claude/docs/ecm-pipeline.md) — HPPC extraction, capacity detection, fitting, outputs.
