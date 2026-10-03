# CloudGuard

**AI-powered cloud security and cost optimisation.**

CloudGuard ingests cloud billing and network telemetry, runs it through two
machine-learning pipelines, and turns the output into decisions an operator can
act on:

- **Cost intelligence** — Prophet forecasts daily spend, projects the month-end
  total with a confidence interval, and flags the services driving an overrun.
- **Security analytics** — an IsolationForest scores behavioural windows from VPC
  flow logs and surfaces statistically rare events, with no rule written for any
  specific attack pattern.

Every number on the dashboard comes out of a real pipeline. The cloud account is
mocked (Moto), but EC2, CloudWatch and CloudWatch Logs calls genuinely round-trip
through the AWS API surface, and the models really fit on the data that comes
back. Nothing in the UI is hardcoded.

---

## Contents

- [Quick start](#quick-start)
- [Architecture](#architecture)
- [How the data is real](#how-the-data-is-real)
- [The two ML pipelines](#the-two-ml-pipelines)
- [Dashboard](#dashboard)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Testing](#testing)
- [Demo script](#demo-script)
- [Evaluation metrics](#evaluation-metrics)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [Extending CloudGuard](#extending-cloudguard)

---

## Quick start

### Option A — Docker (everything at once)

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| Dashboard | <http://localhost:5173> |
| API docs (Swagger) | <http://localhost:8000/docs> |
| API health | <http://localhost:8000/api/health> |

The public marketing site lives in its own folder outside this repo
(`../cloudguard-site`) and is deployed independently:

```bash
cd ../cloudguard-site && npm install && npm run dev   # http://localhost:5174
```

Then press **Refresh data** in the dashboard, or:

```bash
curl -X POST http://localhost:8000/api/dashboard/refresh
```

The stack starts with empty caches by design — the dashboard shows which
pipelines have not run yet rather than inventing numbers.

### Option B — run the pieces yourself

Four terminals. Python 3.10+ and Node 20+.

```bash
# 1. Redis (broker + cache)
docker run -d --name cloudguard-redis -p 6379:6379 redis:7-alpine

# 2. Backend dependencies
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r backend/requirements.txt
cp .env.example .env

# 3. Celery worker            (from backend/)
cd backend
celery -A app.workers.celery_app worker --loglevel=info -P solo   # -P solo on Windows

# 4. API                      (from backend/, second terminal)
uvicorn app.main:app --reload --port 8000

# 5. Frontend                 (from frontend/, third terminal)
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

Optionally, for periodic collection:

```bash
celery -A app.workers.celery_app beat --loglevel=info
```

### No Redis? It still runs.

If Redis is unreachable, CloudGuard degrades instead of failing:

- The cache falls back to an in-process TTL map.
- Tasks run on a local thread pool, and the API response says
  `"executor": "inline"` rather than pretending Celery handled it.
- `/api/health` reports exactly which dependency is down, and the UI shows a
  degraded-mode banner.

With Redis up but no worker running, tasks are queued and wait for a worker;
`/api/health` says "No workers responded - queued tasks wait until a worker
starts".

Only an outage triggers the fallback. If the broker is reachable but refuses a
task — a TLS option Celery rejects, wrong credentials — the API answers
`503 task_dispatch_failed` and logs the cause. Running the job on the web
process instead would hide a broken deployment behind a successful response.

This is a fallback, not the architecture — with Redis and a worker running, the
full asynchronous path is used.

### Standalone mode: one process, on purpose

Set `REDIS_URL=none` to run without Redis and without a worker by design — for
example as a single free web service. The API then never tries to reach Redis or
a broker, keeps results in its own memory, and runs analysis jobs on its own
threads. Because that is the intended setup rather than an outage,
`/api/health` reports `"status": "ok"` with `"mode": "standalone"` and lists only
the API and the cloud provider, and the dashboard shows a neutral "mode:
standalone" line instead of the degraded warning. A configured Redis that is down
is still reported as degraded.

What standalone mode gives up: results are lost whenever the process restarts
(on a free host, whenever it sleeps), and nothing refreshes on a schedule — press
**Refresh data**.

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                    React + Vite dashboard                        │
│  Overview · Cost · Security · Resources · AI Insights            │
└───────────────┬──────────────────────────────────┬───────────────┘
                │ GET /api/dashboard               │ POST /api/*/run
                │ (cached, always 200)             │ (202 + task_id)
                ▼                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                      FastAPI + Pydantic                          │
│   Reads cache · queues work · never fits a model in a request    │
└───────────────┬──────────────────────────────────┬───────────────┘
                │                                  │
        read cached results                 enqueue task
                │                                  │
                ▼                                  ▼
┌───────────────────────────┐      ┌───────────────────────────────┐
│           Redis           │◀────▶│       Celery workers          │
│   cache + broker + task   │      │  ingest · forecast · detect   │
│         registry          │      │         · refresh_all         │
└───────────────────────────┘      └───────┬───────────────┬───────┘
                                           │               │
                          ┌────────────────▼──┐     ┌──────▼─────────┐
                          │  Cloud data layer │     │ ML processing  │
                          │  boto3 → Moto     │     │ Prophet        │
                          │  EC2 · CloudWatch │     │ IsolationForest│
                          │  Logs · Cost Expl.│     │                │
                          └───────────────────┘     └────────────────┘
```

**The contract that makes it asynchronous:** a `POST` to any `/run` endpoint
returns `202` with a `task_id` immediately. The client polls
`GET /api/tasks/{task_id}` until `COMPLETED`. A Prophet fit takes seconds; the
request that triggers it takes milliseconds.

```text
POST /api/costs/forecast/run   →   202 {"task_id": "abc", "status": "PENDING"}
GET  /api/tasks/abc            →   {"status": "PROCESSING", "progress": 45,
                                    "stage": "Fitting Prophet model"}
GET  /api/tasks/abc            →   {"status": "COMPLETED", "result": {...}}
GET  /api/costs/forecast       →   the cached forecast
```

### Why a durable task registry

Celery's own result backend expires, and a `PENDING` state from Celery is
ambiguous — an unknown task id and a not-yet-started task look identical. Every
task therefore also gets a record in Redis with its type, stage, progress,
duration, result summary and error. `GET /api/tasks/{id}` answers precisely,
including after a worker restart, and returns a real `404` for an id that never
existed.

---

## How the data is real

This is the part that distinguishes CloudGuard from a mockup.

| Source | How it works | Genuinely round-trips? |
|---|---|---|
| **EC2** | `run_instances` seeds a tagged fleet into Moto; `describe_instances` reads it back | ✅ yes |
| **CloudWatch** | `put_metric_data` publishes CPU/network datapoints; `get_metric_statistics` reads them back | ✅ yes |
| **CloudWatch Logs** | Behavioural records are written with `put_log_events` and read with `filter_log_events` | ✅ yes |
| **Cost Explorer** | Moto accepts `GetCostAndUsage` but returns an empty stub, so a wire-compatible synthetic response is used | ⚠️ parser is real, transport is not |

On the Cost Explorer caveat: the synthetic payload is built in the **exact shape
of a real `GetCostAndUsage` response** (`ResultsByTime → Groups → Keys/Metrics`,
amounts as strings). The parser in [`backend/app/cloud/aws.py`](backend/app/cloud/aws.py)
is production code — pointing `CLOUD_MODE=real` at a real AWS account exercises
the same parsing path. The provider attempts the native call first and only falls
back when it returns nothing, and `/api/health` reports which mode is in effect
(`"cost_explorer": "synthetic-wire-compatible"`).

Instance CPU shown in the UI is **read back out of CloudWatch**, not taken from
the generator — that join is asserted in
[`test_cloud_integration.py`](backend/tests/test_cloud_integration.py).

### Deterministic by construction

All demo data is seeded from `DEMO_SEED`, and the diurnal patterns are phased on
**record index rather than wall-clock time**. That matters: an earlier version
keyed the pattern to `datetime.now().hour`, which silently changed the anomaly
count and the evaluation metrics depending on what time of day you ran the demo.
The same seed now reproduces the same dataset, the same forecast and the same
precision/recall every time.

The generated cost history deliberately contains **two missing days**, so the
cleaning stage is genuinely exercised rather than merely present. The UI reports
this as "interpolated 2 missing day(s)".

---

## The two ML pipelines

### Cost forecasting — Prophet

```text
Cost Explorer records
   ↓  clean      drop non-numeric / NaN / inf, floor refunds at 0
   ↓  aggregate  per-service rows → one daily total
   ↓  gap-fill   reindex to a continuous calendar, interpolate
   ↓  fit        Prophet (weekly seasonality, multiplicative)
   ↓  project    month-to-date actuals + forecast for remaining days
   ↓  assess     budget breach, trend, per-service risk attribution
```

`changepoint_prior_scale` is **0.01**, tuned on the hold-out backtest. The
default 0.08 chased the step change in the history and scored *worse* than a
seasonal-naive baseline; 0.01 beats that baseline by roughly 19% MAE.

Per-service projections are allocated from the aggregate forecast, weighted by
each service's recent spend and its own growth rate. They reconcile to the
aggregate exactly — asserted in the test suite — which is why RDS can be the top
projected contributor even though EC2 has the larger historical total.

**If Prophet cannot fit** (a broken Stan backend, for example), a deterministic
trend + weekly-seasonality estimator takes over, and the response and the UI both
say so rather than passing the numbers off as Prophet's.

### Anomaly detection — IsolationForest

```text
VPC flow-log windows
   ↓  features   7 behavioural metrics, log1p + standardised
   ↓  fit        IsolationForest (contamination 0.025)
   ↓  score      decision_function → logistic map → 0-100
   ↓  classify   forest decision AND score ≥ threshold
   ↓  explain    rank feature z-scores → label, context, action
```

Two design decisions carry the demo's central claim:

1. **The model is unsupervised.** Seeded outliers carry a `simulated_anomaly`
   flag, but it is never a feature. A test asserts that scores are *bit-identical*
   whether or not the label is present — so the flag demonstrably cannot leak into
   the model.
2. **Event names come from feature attribution, not from the injected label.** An
   event is called "Network transfer spike" because `network_transfer` is the
   feature furthest from the learned baseline, not because the generator labelled
   it so. That is what makes "the model found it, not an `if` statement" an honest
   claim, and it is asserted in
   [`test_security_module.py`](backend/tests/test_security_module.py).

Scores map through a logistic transform rather than a per-batch min-max, so a
score of 90 means the same thing across runs instead of being rescaled by
whatever happened to be in the current batch.

---

## Dashboard

A welcome screen, then five pages; dark theme, built for phones as well as
desktops (see [On phones](#on-phones)).

| Page | What it shows |
|---|---|
| **Welcome** (`/`) | Animated radar-and-orbit hero, live headline figures once the pipelines have run, **Enter dashboard** (or press Enter). It pings the API on arrival, so a sleeping free-tier host wakes while the visitor reads. Deep links such as `#/cost` skip it; the sidebar logo returns to it. |
| **Overview** | Headline tiles, spend trajectory, live event feed, service spend, top insight, per-pipeline status |
| **Cost Intelligence** | Forecast chart with confidence band, month-end projection, budget position, backtest accuracy, per-service table |
| **Security Analytics** | Anomaly timeline with decision threshold, filterable event feed, detail drawer with feature deviations |
| **Cloud Resources** | EC2 inventory with search and filters, utilisation, idle waste, optimisation hints |
| **AI Insights** | Every insight as metric + model signal + action, grouped and ranked by severity |

### Visualisation decisions

Charts are hand-rolled SVG — no chart library — which keeps the entry bundle at
**~69 KB gzipped** (59.4 KB of JavaScript plus 9.5 KB of CSS) and gives exact
control over the marks. The Outfit typeface ships with the app as a variable
font (a 32 KB file covers Latin text), so nothing loads from a font CDN and the
design's in-between weights render exactly.

Motion is CSS only and animates `transform` and `opacity`, so it stays on the
compositor: the welcome screen's sweep and orbits, its exit into the dashboard,
and a short fade as each page arrives. Page chunks are prefetched when the
browser is idle, so navigating never flashes a loading skeleton. Everything
honours `prefers-reduced-motion`.

- **Series colours are validated for colour-vision deficiency.** Observed spend
  (blue `#3987e5`) against forecast (orange `#d95926`) measures ΔE 26.8 under
  protanopia on this surface, well clear of the ≥8 threshold.
- **Status colour never carries meaning alone.** Critical-red and good-green are
  nearly indistinguishable under deuteranopia, so every status badge ships with
  its label text, and the forecast series is dashed as well as differently
  coloured.
- **Service spend uses one hue, not eight.** Bar length encodes the magnitude;
  eight hues would be a rainbow encoding nothing. Risk-flagged services switch to
  a status colour *and* gain an icon and a "Risk" label.
- **One y-axis per chart, always.** No dual-axis plots.
- Event rows are keyboard-reachable (`tabIndex`, Enter/Space) with a visible focus
  ring, not mouse-only.

### On phones

Checked in a 390 × 844 touch viewport on every page and overlay: nothing
scrolls sideways, no table needs swiping, every control is at least 44 px tall,
and no text is under 11 px. The phone rules apply only below 640 px wide or on
touch screens, so the desktop layout is unaffected.

- **Top bar** — one line: the title truncates with an ellipsis, search and
  refresh become icon buttons (the refresh label stays as the accessible name),
  and the "Updated…" note is dropped.
- **Panel headers** — buttons move under the title when both no longer fit.
- **Tables** — keep their essential columns (Security: time, event, score,
  status; Resources: resource, state, CPU, cost; Cost: service, trend,
  projection, risk). A resource's type, environment and note move under its
  name rather than disappearing.
- **Inputs are 16 px**, so iOS Safari does not zoom the page when one is tapped.
- **Touch screens** get 44 px targets and no keyboard-only hints (`Ctrl K`,
  "press Enter", arrow-key legends).

### Loading, fallback and error states

The UI distinguishes situations that need different responses, rather than
showing one generic error:

| Situation | What the user sees |
|---|---|
| First load | Skeleton loaders shaped like the real content |
| Pipeline has not run | "Analysis has not run yet" + a **Run** button, naming the pipeline |
| Background refresh failed | Previous data stays on screen, marked stale, with a Retry |
| API unreachable | "Cannot reach the API" — no hosts or ports, since visitors see it |
| Something other than the API answered | "Unexpected response from the API" (e.g. a static host's HTML page because `VITE_API_BASE_URL` is wrong) |
| A refresh was refused | The reason next to **Refresh data** (rate limit, broker fault) |

A refetch never blanks the screen: data stays mounted while a refresh is in
flight, so the dashboard updates smoothly instead of flashing.

---

## Data freshness

Expiry and staleness are deliberately separate concerns:

* **Redis TTL** decides how long a payload is served at all.
* **`generated_at`** decides how old it is allowed to be before the UI warns.

Each freshness threshold is set *below* its cache TTL, which is what makes the
stale window reachable — the payload keeps being served for the rest of its TTL,
but the dashboard stops presenting it as current.

| State | Meaning | UI |
|---|---|---|
| `fresh` | within the threshold | normal |
| `stale` | still served, past the threshold | amber banner naming the pipeline and its age |
| `expired` | TTL elapsed, but the pipeline has succeeded before | "last successful run N ago" |
| `unavailable` | never ran, or the timestamp is unreadable | run prompt |

A long-lived `cloudguard:lastsuccess:*` marker outlives each payload's TTL, which
is what lets an expired cache still report when the data was last good.

> Historical note: age used to be derived from Redis' *remaining* TTL
> (`age = ttl_total - remaining`). That bounds age by the TTL, so the `age > ttl`
> staleness test could never fire and the entire stale path was dead code.
> `backend/tests/test_freshness.py` pins the corrected behaviour.

---

## Security

Authentication and rate limiting apply to the endpoints that queue ML work.
Read endpoints stay open so the dashboard remains a drop-in demo.

```bash
CLOUDGUARD_API_KEY=your-key-here     # empty disables auth (local demo default)
RATE_LIMIT_WRITE_REQUESTS=10         # per window, 0 disables
RATE_LIMIT_WINDOW_SECONDS=60
```

With a key configured, protected endpoints require `X-API-Key`:

| Request | Response |
|---|---|
| no key | `401 api_key_required` |
| wrong key | `403 api_key_invalid` |
| correct key | `202` with a task id |
| over the rate limit | `429` with `Retry-After` |

The key is compared in constant time and never appears in a response or a log
line. The frontend sends it when `VITE_API_KEY` is set at build time.

**Rate-limit buckets.** With auth on, each verified key is one bucket. With auth
off — the mode a public dashboard needs, because a key cannot be shipped in the
browser bundle — requests are bucketed by client IP, and any `X-API-Key` header
is ignored for bucketing. (It used to pick the bucket, so rotating a random key
per request bypassed the limit entirely.) Behind a reverse proxy the client IP
is only correct once `FORWARDED_ALLOW_IPS` is set — see
[Client IPs behind a proxy](#client-ips-behind-a-proxy).

**Connection strings stay private.** `/api/health` reports the broker's
transport and TLS only, never its URL — a managed `REDIS_URL` embeds the
password. Log lines carry redacted URLs (`rediss://***@host:6380/0`), and a
filter on the log handler masks credentials in any message or traceback a
library emits, as a backstop.

**Shared demo data.** "Inject test anomaly" appends an event to data every
visitor sees, and it stays until the API restarts. So it is refused with
`403 anomaly_injection_disabled` when `ENVIRONMENT=production`, and the button
and its command-palette entry are hidden there. Set `ALLOW_ANOMALY_INJECTION`
to `true` or `false` to decide explicitly, for example `true` on a private
demo. The check runs on the server, so it holds for direct API calls too.

**CORS.** Credentials are off (`allow_credentials=False`): the API authenticates
with a header, never a cookie, so no cross-origin request needs them.
`CORS_ORIGINS` never becomes `*`; an empty value falls back to an explicit
localhost allowlist, so a missing setting cannot open the API to every origin.

---

## API reference

Interactive docs: <http://localhost:8000/docs>

### Read endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/health` | API, Redis, Celery and cloud-provider status |
| `GET /api/dashboard` | Everything the dashboard needs, in one call |
| `GET /api/insights` | Human-readable ML insights |
| `GET /api/costs` · `/api/costs/history` | Daily and per-service spend |
| `GET /api/costs/forecast` | Prophet forecast, projection, budget, accuracy |
| `GET /api/security` | Posture, severity breakdown, precision/recall |
| `GET /api/security/events` | Scored feed — `limit`, `offset`, `status`, `min_score` |
| `GET /api/security/anomalies` | Anomalies only, highest score first |
| `GET /api/security/events/{id}` | One event with feature deviations |
| `GET /api/cloud` | Aggregate estate health |
| `GET /api/cloud/resources` | Inventory — `status`, `environment`, `search`, `idle_only` |
| `GET /api/cloud/metrics` | CloudWatch series |
| `GET /api/tasks` · `/api/tasks/{id}` | Background task status |

### Work endpoints (all return `202` with a `task_id`)

| Endpoint | Body options |
|---|---|
| `POST /api/dashboard/refresh` | `force` |
| `POST /api/costs/forecast/run` | `horizon_days`, `force` |
| `POST /api/costs/ingest` | `days` (query) |
| `POST /api/security/anomalies/run` | `contamination`, `inject_anomaly`, `force` |
| `POST /api/security/ingest` | `inject_anomaly` |
| `POST /api/cloud/ingest` | — |

### Status codes

| Code | Meaning |
|---|---|
| `200` | Cached results |
| `202` | Work accepted; poll the task |
| `404` | Unknown task or event id |
| `401` / `403` | Missing / wrong `X-API-Key` (only when auth is enabled) |
| `403` | `anomaly_injection_disabled`: test-anomaly injection is off on this deployment |
| `422` | Validation failure, or too little data to model |
| `429` | Rate limit exceeded — `Retry-After` says when to try again |
| `502` | Cloud API failure |
| `503` | Pipeline has not produced results yet — includes `run_endpoint` to call |
| `503` | `task_dispatch_failed`: the broker is reachable but refused the task (configuration) |

`503` rather than `404` for a cold pipeline is deliberate: the route exists, the
data does not yet, and the response tells the client how to fix it.

---

## Configuration

All settings are environment variables; see [`.env.example`](.env.example) for the
annotated list. The ones worth knowing:

| Variable | Default | Why you would change it |
|---|---|---|
| `CLOUD_MODE` | `moto_inproc` | `moto_server` for a shared mock, `real` for real AWS |
| `REDIS_URL` | `redis://localhost:6379/0` | Point at your Redis; `rediss://` (TLS) gets `ssl_cert_reqs=required` added for Celery; `none` runs [standalone](#standalone-mode-one-process-on-purpose) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Read by uvicorn: proxies trusted to report the client IP — see [Client IPs behind a proxy](#client-ips-behind-a-proxy) |
| `DEMO_SEED` | `1337` | Changes the entire demo dataset — keep fixed for reproducibility |
| `MONTHLY_BUDGET` | `14000` | Drives the budget-breach warning |
| `PROPHET_CHANGEPOINT_PRIOR_SCALE` | `0.01` | Higher = trend follows recent changes more eagerly |
| `ANOMALY_CONTAMINATION` | `0.025` | Expected outlier rate; higher flags more |
| `ANOMALY_SCORE_THRESHOLD` | `65` | Minimum 0-100 score to report an anomaly |
| `ALLOW_ANOMALY_INJECTION` | unset: off only when `ENVIRONMENT=production` | Turn "Inject test anomaly" on for a private demo, or off anywhere |
| `CACHE_TTL_FORECAST` | `3600` | How long a forecast stays warm |

`CORS_ORIGINS` accepts either `a,b` or a JSON array.

---

## Testing

```bash
# Backend — 353 tests
cd backend
pytest                      # or: pytest -v
pytest tests/test_cost_module.py        # cost pipeline only
pytest -k "anomaly or security"         # security pipeline only

# Frontend — 143 tests, plus lint
cd frontend
npm test
npm run test:watch
npm run lint
```

The backend suite runs with **Redis deliberately unreachable**, so it passes on a
machine with nothing else running and exercises the degradation paths on every
run. Coverage by area:

| File | Tests | Covers |
|---|---|---|
| `test_api.py` | 44 | Endpoint contracts, validation, cold-start 503s, OpenAPI completeness |
| `test_async_architecture.py` | 34 | Task lifecycle, caching, TTLs, failure handling, partial refresh |
| `test_cost_module.py` | 27 | Cleaning, gap-filling, Prophet, projection, MAE vs baseline, fallback engine |
| `test_security_module.py` | 22 | Feature prep, scoring, label attribution, precision/recall, label isolation |
| `test_cloud_integration.py` | 26 | Moto EC2/CloudWatch/Logs round-trips, Cost Explorer parsing, determinism |
| `test_config.py` | 16 | Settings parsing, `.env.example` validity |
| `test_hardening.py` | 50 | Compression, health latency, API keys, rate limits, CORS, payload shape, task batching |
| `test_freshness.py` | 36 | The four freshness states, timestamp parsing, malformed input |
| `test_deployment_security.py` | 98 | No secrets in `/api/health` or logs, rate-limit identity, trusted proxies, scoped CORS, Redis TLS for Celery, dispatch failures, reported model config, anomaly-injection control, standalone mode |

Frontend (Vitest + Testing Library): `dashboard.test.jsx` 44, `units.test.jsx` 43,
`palette.test.jsx` 25, `hardening.test.jsx` 22, `landing.test.jsx` 9 — page
states, the API client (including non-JSON responses), charts, the command
palette, the welcome screen and its routing, and the static hosting config
(favicon, `vercel.json`, Node pin).

Notable cases, because they encode the claims this project makes:

- `test_ground_truth_never_reaches_the_model` — scores are identical with and
  without the label, proving no leakage.
- `test_labels_come_from_the_dominant_feature_not_a_rule` — event names follow
  feature attribution.
- `test_backtest_reports_mae_and_beats_the_naive_baseline` — hold-out, not
  in-sample.
- `test_projection_allocates_the_whole_aggregate` — per-service projections
  reconcile with the aggregate forecast.
- `test_refresh_survives_one_failing_stage` — a partial refresh still publishes
  what succeeded.

---

## Demo script

### Demo A — cost forecasting

```bash
# 1. Trigger the pipeline; note it returns immediately
curl -X POST http://localhost:8000/api/dashboard/refresh \
     -H 'Content-Type: application/json' -d '{"force":true}'

# 2. Poll the task through its stages
curl http://localhost:8000/api/tasks/<task_id>

# 3. Read the forecast
curl http://localhost:8000/api/costs/forecast | python -m json.tool
```

In the UI: open **Cost Intelligence**. The chart shows observed spend joining a
30-day Prophet forecast with its confidence band. The tiles show month-to-date,
the projected month-end total, budget position and the backtest MAE. The warning
banner names the service driving the overrun.

The point to make: pressing **Retrain** keeps the UI fully interactive while a
Prophet fit runs on the worker.

### Demo B — security anomaly

1. Open **Security Analytics**. The timeline shows a dense band of routine
   windows below the decision threshold and a handful of flagged outliers above
   it.
2. Press **Inject test anomaly**. This appends a genuinely new record to the
   mocked CloudWatch Logs group and re-runs detection on the worker. (The button
   is hidden where `ENVIRONMENT=production`; set `ALLOW_ANOMALY_INJECTION=true`
   to demo it there.)
3. The anomaly count rises by one and a new event appears at the top of the feed.
4. Click the event. The drawer shows *why* it was flagged: which features
   deviated, by how many standard deviations, and what to do about it.

The point to make: no rule was written for that event. The model found it because
it is statistically rare, and the label came from which feature was most extreme.

```bash
# The same flow from the CLI
curl -X POST http://localhost:8000/api/security/anomalies/run \
     -H 'Content-Type: application/json' -d '{"inject_anomaly":true}'
curl http://localhost:8000/api/security | python -m json.tool
```

### Demo C — resilience

Stop Redis and press **Refresh data**. The task still completes — the response
reports `"executor": "inline"` and the UI badges it — and the dashboard keeps
serving from the in-process cache while the sidebar shows which dependency is
down. (Stopping only the worker is different: Redis still accepts the task, so
it waits in the queue until a worker returns, and `/api/health` says so.)

---

## Evaluation metrics

Measured on the seeded demo dataset (`DEMO_SEED=1337`), reproducible on any
machine.

### Forecast accuracy — 14-day hold-out backtest

| Metric | Value |
|---|---|
| MAE | ~$9.84 / day |
| MAPE | ~2.2% |
| RMSE | ~$12.16 |
| Seasonal-naive baseline MAE | ~$12.18 / day |
| **Skill vs baseline** | **~+19%** |

Reported against a baseline deliberately: an MAE with nothing to compare it to
says very little.

### Anomaly detection — against seeded ground truth

| Metric | Value |
|---|---|
| Precision | 1.00 |
| Recall | 1.00 |
| F1 | 1.00 |
| TP / FP / FN | 4 / 0 / 0 (5 / 0 / 0 after injecting the live anomaly) |

Honest caveat, also stated in the API response: ground truth covers only the
deliberately seeded outliers. A flagged record outside that set counts against
precision even if it is a genuine statistical outlier.

### Architectural performance

| Measure | Observed |
|---|---|
| Cached read endpoints | 8–30 ms |
| `/api/health` | 11–18 ms (was ~970 ms before the ping fix) |
| Task submission (`POST /run`) | 50–250 ms, against ~5 s of queued work |
| Full refresh on the worker | ~5 s (8 resources, 944 cost records, 240 events) |
| Dashboard payload | 36 KB raw / **7 KB gzipped** (was 108 KB uncompressed) |
| Frontend entry bundle | 182 KB raw / 59.4 KB gzipped JS + 40 KB / 9.5 KB CSS + a 32 KB self-hosted font; the five pages load lazily (4–13 KB raw each) and are prefetched when idle |

Every response carries an `X-Process-Time-Ms` header, and the API logs any
request over 1 s.

---

## Deploying to Vercel

Vercel hosts the two frontends well. It cannot host the backend — see below.

| Part | Vercel? | Why |
|---|---|---|
| **Marketing site** (`../cloudguard-site`) | ✅ | Separate folder; static, no API calls |
| **Dashboard** (`frontend/`) | ✅ | Static SPA; talks to the API over CORS |
| **API + worker + Redis** | ❌ | Needs long-running processes and state |

Both apps ship a `vercel.json`, so there is nothing to configure beyond the root
directory and one environment variable.

### 1. Marketing site — two minutes, zero config

The site is its own folder (`../cloudguard-site`), so give it its own Vercel
project. It is not currently a git repository; see *Deploying it* below.

| Setting | Value |
|---|---|
| Root Directory | *(repository root)* |
| Framework Preset | Vite *(auto-detected)* |
| Build Command | `npm run build` *(from vercel.json)* |
| Output Directory | `dist` *(from vercel.json)* |

No environment variables. It makes no network calls except Google Fonts.

### 2. Dashboard — one variable, and CORS must agree

Create a **second** Vercel project from the same repo:

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Environment Variable | `VITE_API_BASE_URL` = `https://your-api.example.com` |
| Node.js Version | 22.x — taken from `engines` in `package.json` |

`VITE_API_BASE_URL` is read at **build** time, so changing it requires a
redeploy, not just a settings save. If it is missing or wrong, `/api/*` requests
reach the static host itself. Each dashboard section is a page at its own path
(`/overview`, `/cost`, `/security`, `/resources`, `/insights`), so `vercel.json`
rewrites page paths to `index.html` — but deliberately not `/api/*`, so those
requests fail visibly with a 404 instead of the dashboard waiting forever. The
welcome screen reads that 404 (or an HTML answer) as "not connected" within a
few seconds, and a CORS refusal as "refusing this site", rather than showing
"Waking…" for minutes; the browser console names the setting to fix.

Then allow the Vercel origins on the API:

```bash
CORS_ORIGINS=https://cloudguard-dashboard.vercel.app
# Preview URLs are <project>-git-<branch>-<scope>.vercel.app. End the pattern with
# YOUR scope: without it, anyone's project named cloudguard-dashboard-git-* matches.
CORS_ORIGIN_REGEX=https://cloudguard-dashboard-git-[a-z0-9-]+-<your-vercel-scope>\.vercel\.app
```

`<your-vercel-scope>` is your Vercel team's slug: the last segment of any preview
URL (`cloudguard-dashboard-git-main-<scope>.vercel.app`), also shown in the team
settings. It is not stored in this repository, so it must be filled in by hand.

Without the regex, production works and every preview deployment silently fails
in the browser with no `access-control-allow-origin` header. `*.vercel.app` names
are shared by every Vercel user, so a pattern on them narrows rather than
guarantees; for a hard guarantee, list exact origins or serve previews from your
own domain. The exposure is small either way: credentials are off and the read
endpoints are public by design.

> **Never set `VITE_API_KEY` in Vercel.** Vite inlines `VITE_*` variables into
> the JS bundle, so the key ships to every visitor in plain text. It is for
> server-to-server callers only. See [Known limitations](#known-limitations).

### 3. The backend goes somewhere else

The API is only one of four processes. Vercel's functions are request-scoped and
stateless, which breaks three of them:

- **Celery worker** — a long-running consumer, not a request handler.
- **Celery beat** — a scheduler that must always be on.
- **Moto's mocked AWS** — in-process state seeded once per process; a serverless
  cold start would re-seed on every invocation.

Putting the API behind serverless would also defeat the design: work is queued
*off* the request path on purpose, and a function that returns kills whatever it
spawned.

**Free option: one Render web service.** Render's free plan covers web services
but not background workers, so run the API alone in
[standalone mode](#standalone-mode-one-process-on-purpose): New → Web Service →
this repository, Language **Docker**, Root Directory `backend`, Instance Type
**Free**, Health Check Path `/api/health`, and these variables:

```bash
PORT=8000
ENVIRONMENT=production
REDIS_URL=none
CORS_ORIGINS=https://<your-dashboard>.vercel.app   # no trailing slash
```

Do not add a Redis (Key Value) instance without a worker: jobs would be queued
with nothing to run them.

For the full setup with a worker and scheduled refreshes, any container host
runs the existing `docker-compose.yml` as-is:

| Host | Notes |
|---|---|
| **Railway** / **Render** | Compose-like, managed Redis add-on |
| **Fly.io** | Good fit for the always-on worker and beat |
| **AWS ECS / Lightsail**, **DigitalOcean** | Plain container hosting |

#### Managed Redis over TLS

Redis can be managed (Upstash, Redis Cloud). Those use `rediss://` URLs, which
Celery refuses unless the URL says how to verify the server certificate
("A rediss:// URL must have parameter ssl_cert_reqs"). CloudGuard appends
`ssl_cert_reqs=required` when it is missing — to `REDIS_URL`, `CELERY_BROKER_URL`
and `CELERY_RESULT_BACKEND`, including Celery's own environment variables, which
Celery reads ahead of the app's configuration. So this is enough for the API and
the worker:

```bash
REDIS_URL=rediss://default:<password>@<host>:6379
```

An explicit `ssl_cert_reqs` is left alone. If you set it yourself, use lower-case
`required`: both Celery and redis-py accept that spelling.

#### Client IPs behind a proxy

With auth off, the rate limit is per client IP. Behind a platform proxy the TCP
peer is the proxy, so uvicorn (started with `--proxy-headers`) replaces it with
an address from `X-Forwarded-For` — but only when the peer is listed in
`FORWARDED_ALLOW_IPS`. It walks the header right to left, skipping trusted hops,
so entries a client writes itself are never used. CIDR ranges need uvicorn 0.31+
(pinned in `requirements.txt`).

- **Default (`127.0.0.1`)** — safe, but behind a platform proxy every visitor
  shares one bucket.
- **Never `*`** — uvicorn then takes the *left-most* entry, which the client
  controls, so anyone can pick their own address. The API logs a warning at
  startup if it sees this.

There is no universal value. Starting points, then confirm on the live
deployment:

| Platform | What its proxy does | `FORWARDED_ALLOW_IPS` |
|---|---|---|
| Railway | Edge appends the real client as the right-most entry ([Railway staff](https://station.railway.com/questions/edge-proxy-x-forwarded-for-and-x-real-ip-c5a50049)); proxy peers have been observed in `100.64.0.0/10` | `100.64.0.0/10` |
| Render | Traffic passes Cloudflare and Render's load balancer, and client-supplied entries are kept. Render says to trust "the private IPs or CIDR blocks of your managed load balancers" but publishes no range ([Render](https://render.com/articles/fastapi-production-best-practices)) | Find the peer range with the check below |
| Fly.io | The right-most entry is *your app's own* shared/dedicated IP ([Fly docs](https://docs.fly.io/networking/request-headers)) | The proxy's peer range **plus** your app's IPs from `fly ips list` — otherwise every visitor resolves to your app's IP |
| AWS ALB | Appends the client IP; ALB nodes connect from inside your VPC ([AWS](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/x-forwarded-headers.html)) | Your VPC CIDR, e.g. `10.0.0.0/16` |
| Local `docker compose` | nginx on a dynamic bridge network | Leave the default; local users share one bucket |

If a CDN sits in front (Railway's Fastly path, Render's Cloudflare), the
right-most untrusted address can be the CDN's edge rather than the visitor —
still unspoofable, just coarser. Add the CDN's published ranges for per-visitor
limits.

**Confirm it:** set `LOG_LEVEL=DEBUG`, press **Refresh data** once, and find
`Rate-limit bucket ip:… (client=…, x-forwarded-for=…)` in the API log. With the
default setting, `client` is the proxy's own address — the range to trust. Once
`FORWARDED_ALLOW_IPS` is right, `client` is your own public IP. Then restore
`LOG_LEVEL`.

### Resulting topology

```text
  cloudguard.vercel.app          static, no backend
          │
          │  "Live demo" link
          ▼
  dashboard.vercel.app  ──CORS──►  api.your-host.com
     (static SPA)                   FastAPI + Celery + beat + Redis
```

Three independent deploys. The marketing site stays up even if the API is down.

---

## Project structure

```text
cloudguard/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI app, middleware, error handlers
│   │   ├── config.py            Environment-driven settings
│   │   ├── cache.py             Redis with in-process fallback
│   │   ├── connection_urls.py   Credential redaction, rediss:// defaults for Celery
│   │   ├── logging_config.py    Shared logging, credential-scrubbing filter
│   │   ├── api/                 health · dashboard · costs · security · cloud · tasks
│   │   ├── schemas/             Pydantic contracts
│   │   ├── services/            Pipeline orchestration + cached reads
│   │   ├── ml/
│   │   │   ├── forecasting.py          Prophet + fallback, projection, backtest
│   │   │   └── anomaly_detection.py    IsolationForest, scoring, evaluation
│   │   ├── workers/             Celery app, tasks, beat schedule
│   │   └── cloud/
│   │       ├── base.py          Provider-neutral interface (multi-cloud ready)
│   │       ├── aws.py           boto3 provider
│   │       ├── moto_setup.py    Mock environment + seeding
│   │       └── datagen.py       Deterministic demo data
│   ├── tests/                   353 tests
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── public/favicon.svg       Copied to the root of dist/ by Vite
│   ├── src/
│   │   ├── api/client.js        Typed errors, timeouts, abort handling
│   │   ├── hooks/               useResource (stale-while-refresh), useTask (polling)
│   │   ├── state/               Dashboard context
│   │   ├── components/          Primitives and app shell
│   │   ├── charts/              Hand-rolled SVG charts
│   │   ├── pages/               The five dashboard pages
│   │   └── index.css            Design system
│   ├── tests/                   143 tests
│   ├── Dockerfile · nginx.conf
│   ├── vercel.json · .eslintrc.cjs
│   └── package.json             Node 22.x pinned in "engines"
├── docker-compose.yml
├── .env.example
└── README.md
```

The marketing site has moved out of this repository to `../cloudguard-site`. It
is Tailwind + shadcn-style (the stack 21st.dev components are built for), while
the dashboard stays on vanilla CSS as the spec requires. They share no code —
only the brand colours and the validated data-visualisation palette, so the
screenshots and diagrams on that page match the real product.

The separation that matters: `api/` never imports `ml/`. Requests read from
`cache.py`; only `workers/tasks.py` calls the pipelines. That is enforced by
convention and verified by the latency assertions in the test suite.

---

## Known limitations

Stated plainly, because knowing where the edges are is part of the deliverable.

1. **Cost Explorer is not truly mocked.** Moto does not implement
   `GetCostAndUsage` usefully. The response is synthesised in the real API's wire
   format and parsed by production code, but the transport is not exercised. EC2,
   CloudWatch and CloudWatch Logs *are* genuinely round-tripped.
2. **Per-service forecasts are allocated, not individually modelled.** Fitting
   Prophet per service would be slow and noisy on thin series, so the aggregate
   forecast is distributed by recent spend weighted by growth. The split is a
   good attribution, not eight independent forecasts.
3. **Precision/recall only covers seeded anomalies.** There is no labelled
   real-world attack data here. The metric measures whether the model finds known
   injected outliers, which is weaker than a true benchmark.
4. **`moto_inproc` gives each process its own mocked account.** The API and the
   worker hold separate AWS state. It stays consistent because the seed data is
   deterministic, and only the worker writes results. Use `moto_server` for
   genuinely shared mock state.
5. **The Prophet backtest is a single hold-out**, not rolling-origin
   cross-validation. Adequate for model selection here; a production system would
   use `prophet.diagnostics.cross_validation`.
6. **`cmdstanpy` is pinned below 1.3.** Prophet 1.1.6 ships a trimmed CmdStan
   tree with no makefile, which cmdstanpy ≥ 1.3 rejects, making every Stan backend
   fail to load. The pin is documented in `requirements.txt`; the fallback
   forecaster exists for exactly this class of problem.
7. **Injected demo anomalies persist.** "Inject test anomaly" writes a real event
   into the mocked log group, so repeated presses accumulate. Restart the worker,
   or call `reset_flow_logs()`, to return to the seeded baseline. Because every
   visitor shares that state, injection is off by default when
   `ENVIRONMENT=production` (see [Security](#security)).
8. **Per-client rate limiting needs per-platform setup.** Behind a proxy it
   depends on `FORWARDED_ALLOW_IPS` matching that platform's proxies, which no
   single default can do; until it is set, visitors share one bucket. See
   [Client IPs behind a proxy](#client-ips-behind-a-proxy).

---

## Extending CloudGuard

### Switching to real AWS

```bash
CLOUD_MODE=real
AWS_ACCESS_KEY_ID=<real>
AWS_SECRET_ACCESS_KEY=<real>
AWS_REGION=<your region>
```

No code changes. The provider stops starting Moto and issues real calls, and the
Cost Explorer path uses the native API instead of the synthetic response. The IAM
role needs `ec2:DescribeInstances`, `cloudwatch:GetMetricStatistics`,
`logs:FilterLogEvents` and `ce:GetCostAndUsage`.

Two things to adjust for production scale: `describe_instances` is not paginated
here, and Cost Explorer charges per request, so widen `CACHE_TTL_COST`.

### Adding another cloud

Implement [`CloudProvider`](backend/app/cloud/base.py) — four methods:
`list_resources`, `get_metrics`, `get_cost_and_usage`, `get_security_records` —
and return the same dict shapes. The services layer depends only on that
interface, never on boto3, so Azure Cost Management or GCP Billing slots in
without touching the ML, API or UI layers.

### Other directions

- **Automated remediation** — the task framework already carries progress and
  failure states, so a `remediate_idle_resources` task would plug straight in.
- **Rolling-origin cross-validation** — replace the single hold-out in
  `CostForecaster.backtest`.
- **Alerting** — insights already carry severity and an action; routing them to
  Slack or PagerDuty is a new task plus a notifier.
- **Per-service forecasting** — swap the allocation in `analyse_services` for
  individual fits once there is enough history per service.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Dashboard shows "pipelines not yet run" | Normal on a cold start. Press **Refresh data**. |
| `"executor": "inline"` in responses | Celery broker unreachable. Start Redis and the worker. |
| `503 task_dispatch_failed` | The broker is up but refused the task — usually its URL or TLS settings. The API log names the cause. |
| Sidebar shows redis/celery down | Check `docker compose ps`, or that Redis is on port 6379. If you run without Redis on purpose (e.g. a free single web service), set `REDIS_URL=none`. |
| Forecast says "fallback model in use" | Prophet's Stan backend failed to load. Check `cmdstanpy==1.2.4` is installed. |
| `Prophet object has no attribute 'stan_backend'` | cmdstanpy ≥ 1.3 is installed. `pip install cmdstanpy==1.2.4`. |
| Celery worker exits immediately on Windows | Use `-P solo`. |
| Frontend cannot reach the API (local dev) | The Vite dev server proxies `/api` to `http://localhost:8000`; check the API is running there (`VITE_API_PROXY_TARGET` points the proxy elsewhere). |
| Deployed dashboard: 404s or "Unexpected response from the API" | `VITE_API_BASE_URL` is missing or wrong in the Vercel project. Set it to the API's public URL and **redeploy** — it is baked in at build time. |
| Deployed dashboard: CORS errors on preview URLs | `CORS_ORIGIN_REGEX` must end with your Vercel scope — see [Dashboard](#2-dashboard--one-variable-and-cors-must-agree). |
| Every visitor hits the rate limit together | `FORWARDED_ALLOW_IPS` does not include your platform's proxy. See [Client IPs behind a proxy](#client-ips-behind-a-proxy). |
