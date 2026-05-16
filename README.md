# acme-carrier-api

Inbound carrier sales API for the HappyRobot take-home. FastAPI + SQLite, deployed to Fly.io. The HappyRobot voice agent calls this service during a carrier call to search loads, verify the carrier against FMCSA, negotiate price, and post the call summary.

**Live API:** `https://acme-carrier-api.fly.dev`
- [`/health`](https://acme-carrier-api.fly.dev/health) — liveness check (public)
- [`/docs`](https://acme-carrier-api.fly.dev/docs) — interactive OpenAPI / Swagger UI (public)
- All other endpoints require `X-API-Key` header (provided in the submission email)

## Endpoints

All non-`/health` endpoints require `X-API-Key`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Load-balancer health check (public). |
| GET | `/loads/search` | Find a matching load. Filters: `origin`, `destination`, `equipment_type` (fuzzy), `pickup_after`, `pickup_before`, `min_rate`, `limit`. |
| GET | `/carriers/verify?mc_number=...` | FMCSA lookup + eligibility rules. Cached 24h in SQLite. |
| POST | `/negotiations/evaluate` | Deterministic, auditable price negotiation. Body: `{load_id, carrier_offer, round, our_last_offer?}`. |
| POST | `/calls/events` | Idempotent ingest of the HappyRobot call summary, keyed on `call_id`. |
| GET | `/calls` | Paginated call log for the dashboard. Filters: `outcome`, `sentiment`, `since`, `limit`. |
| GET | `/calls/{call_id}` | Full call detail incl. raw payload. |
| GET | `/metrics/today` | One-object KPI tile for today. |
| GET | `/metrics/timeseries?days=14` | Daily rollups for sparklines. |
| GET | `/attention` | The "needs attention" queue — see [Attention rules](#attention-rules). |

OpenAPI docs are mounted at `/docs` only when `ENABLE_DOCS=true`.

## Quick start (local)

```bash
brew install uv
uv sync --extra dev
cp .env.example .env   # then fill FMCSA_API_KEY and API_KEY
uv run uvicorn app.main:app --reload
```

```bash
curl -H "X-API-Key: $API_KEY" 'http://localhost:8000/loads/search?origin=Atlanta&limit=2'
```

## Run with Docker

```bash
docker build -t acme-carrier-api .
docker run --rm -p 8000:8000 \
  -e API_KEY=$API_KEY \
  -e FMCSA_API_KEY=$FMCSA_API_KEY \
  -e ENABLE_DOCS=true \
  -e DB_PATH=/data/app.db \
  -v $(pwd)/data:/data \
  acme-carrier-api
```

`DB_PATH=/data/app.db` points the app at the mounted volume so the SQLite DB persists in `./data/app.db` on the host (matching the Fly.io layout). Drop the `-v` flag and the `DB_PATH` override for an ephemeral container (e.g., for tests).

## Tests

69 unit + integration tests, all passing. The pyramid is intentional: heavy on pure-function unit tests for the negotiation decision table (every branch covered with a row), integration coverage on the FMCSA client (mocked HTTP, real cache layer) and the idempotency contract on `/calls/events`, and no end-to-end tests (HappyRobot platform is the integration surface, and the platform is not available as a test harness).

```bash
uv run pytest -q
```

Core test files:
- `tests/test_negotiation.py` — one row per branch of the decision table.
- `tests/test_fmcsa.py` — mocked FMCSA: eligible, inactive, OOS, missing insurance, not-found, cache hit, cache TTL expiry, MC normalization.
- `tests/test_calls_idempotency.py` — replaying the same `call_id` returns `stored: false`, does not double-count.

## Negotiation decision table

`floor = loadboard_rate * 1.0`, `ceiling = loadboard_rate * 1.12`, `max_rounds = 3`. Counters are quantized to whole dollars. Missing `our_last_offer` on round ≥ 2 falls back to `loadboard_rate` and emits a warning.

| Condition | Decision | Counter / agreed | `reason` |
|---|---|---|---|
| `carrier_offer == loadboard_rate` (any round) | accept | `loadboard_rate` | `parity_with_loadboard` |
| `carrier_offer < loadboard_rate` (any round) | counter | `loadboard_rate` | `below_floor` |
| `loadboard_rate < carrier_offer ≤ ceiling`, round 1 | counter | `midpoint(carrier_offer, our_last_offer_or_loadboard)` | `round_1_no_instant_accept` |
| `loadboard_rate < carrier_offer ≤ ceiling`, round 2 or 3 | accept | `carrier_offer` | `within_margin_band` |
| `carrier_offer > ceiling`, round 1 or 2 | counter | `min(prior + 0.5*(ceiling-prior), ceiling)` | `above_ceiling_concede_half` |
| `carrier_offer > ceiling`, round 3 | reject | — | `above_ceiling_max_rounds` |
| `round > max_rounds` (defensive) | reject | — | `max_rounds_exceeded` |

Logic lives in [app/services/negotiation.py](app/services/negotiation.py) as a pure function; it has zero DB or HTTP dependencies and is the most-tested module.

## FMCSA

We hit the QCMobile docket-number endpoint and cache the response in `fmcsa_cache` (24h TTL) to stay under the free-tier quota during demo recording. Eligibility rules (in [app/services/fmcsa.py](app/services/fmcsa.py)):
- `operating_authority_status == "ACTIVE"` — from `statusCode == "A"` or `allowedToOperate == "Y"`.
- `out_of_service_flag == False` — from `oosDate is None`.
- `insurance_on_file == True` — from `allowedToOperate == "Y"` as a proxy.

`status_summary` strings are agent-readable and read verbatim to the carrier — phrasing is server-controlled so all calls sound consistent.

**Demo affordance — `DEMO_INELIGIBLE_MCS`.** All MCs we use for the demo happen to be active in the live FMCSA registry, which makes the ineligible-rejection branch impossible to show. The env var `DEMO_INELIGIBLE_MCS` (comma-separated, no MC prefix) forces the listed carriers to return `eligible: false` with `reason="operating_authority_inactive"` and the corresponding agent-readable `status_summary`. The override is applied *before* rule evaluation in [app/services/fmcsa.py](app/services/fmcsa.py), so the `details` block stays consistent (`operating_authority_status: "INACTIVE"`) and the real `legal_name` is preserved (the agent still addresses the carrier by name). Configured per-environment via Fly secret. Empty in real production.

## Attention rules

Two card types ship today; structure is a rule registry so adding more is one function in [app/services/attention.py](app/services/attention.py):

- **`floor_too_high`** — loads with 2+ `declined_price` outcomes in the last 7 days. Suggests our ceiling is too tight for the lane.
- **`sentiment_negative_booking`** — booked calls with `negative` or `frustrated` sentiment in the last 7 days. Customer-experience flag on otherwise "successful" calls.

Designed for v1.1 (listed here so reviewers can see the product thinking): repeat ineligible callers; hot lane with low fill rate; agent escalation rate spike; FMCSA cache miss spike.

## Design notes

**Rolling seed dates.** Seed loads use rolling dates — on each container startup, all pickup/delivery dates are computed relative to current UTC, ensuring loads always appear in the near-future window. This makes the system reproducible for reviewers testing days or weeks after submission. Mirrors how real load boards (DAT, Truckstop) maintain live load offers as time-relative postings. Implementation lives in [app/db.py](app/db.py): the JSON template stays anchored to a fixed reference date (readable diffs), and on boot the loads table is wiped and re-inserted with a `today - reference_date` offset applied to every datetime. `call_events` and `fmcsa_cache` are left untouched.

**Two-writes pattern on `/calls/events`.** The HappyRobot workflow posts the same `call_id` twice: a partial write from the `transfer_to_rep` tool *mid-call* (the dashboard's earliest booking signal — `outcome`, `load_id`, `final_price`, `mc_number`, `transfer_attempted`) and a fuller write from the *post-call* enrichment chain (accurate `started_at`/`ended_at`, `duration_seconds`, transcript and recording URLs, AI-classified `sentiment`, extracted `summary`, `negotiation_rounds`). The endpoint does an UPSERT keyed on `call_id` and only overwrites columns that the current request explicitly sent — so the post-call enrichment cannot null out a mid-call value by omitting it. HappyRobot's variable picker does not expose a call-start timestamp at mid-call tool-call time, so `started_at` is optional in the request schema and defaults to server-side UTC `now()`; the post-call write overwrites it with the authoritative timestamp.

**Defensive coercion at LLM boundaries.** During build and testing, the same class of bug appeared seven times: empty strings or unexpected types reaching Pydantic from the LLM-driven enrichment chain (e.g., `final_price: ""` when the agent didn't book, `carrier_eligible: ""` from a downstream classifier). The pattern fix isn't catching each bug one-by-one — it's recognizing that every nullable field at an LLM→API boundary needs the same defensive shape: a `mode="before"` validator that coerces empty strings, blank strings, and obvious sentinels to `None` before type validation runs. Implemented uniformly across [app/models.py](app/models.py) for numerics, datetimes, and bools. New nullable fields get the same treatment by default.

## Security

- `X-API-Key` (long random, generated via `secrets.token_urlsafe(32)`). 401 otherwise.
- HTTPS terminated by Fly.io.
- `slowapi` rate limit at 60 req/min keyed by API key (falls back to IP).
- CORS allowlist via `CORS_ALLOWED_ORIGINS`.
- FMCSA key never logged.
- `ENABLE_DOCS=false` in production so the OpenAPI schema is not public.

**Future hardening:** HMAC-signed webhooks for `/calls/events`. HappyRobot's Webhook-out node does not currently ship signing headers, so we rely on the shared `X-API-Key`. If signing becomes available, wire it as a FastAPI dependency on the route.

## Deployment

```bash
flyctl auth login
flyctl apps create acme-carrier-api
flyctl volumes create acme_data --region fra --size 1
flyctl secrets set API_KEY=... FMCSA_API_KEY=... CORS_ALLOWED_ORIGINS=...
flyctl deploy
```

The first deploy uses `Dockerfile` + `fly.toml`. SQLite is mounted on a 1GB persistent volume at `/data`. The image runs uvicorn directly; for the take-home a single worker is fine.

## File layout

```
acme-carrier-api/
├── app/
│   ├── main.py              # FastAPI app, lifespan, CORS, rate limit
│   ├── auth.py              # X-API-Key dependency
│   ├── db.py                # SQLite engine, schema bootstrap, seed loader
│   ├── routers/
│   │   ├── loads.py         # /loads/search
│   │   ├── carriers.py      # /carriers/verify
│   │   ├── negotiations.py  # /negotiations/evaluate
│   │   ├── calls.py         # /calls/events, /calls, /calls/{id}
│   │   └── metrics.py       # /metrics/today, /metrics/timeseries, /attention
│   ├── services/
│   │   ├── fmcsa.py         # FMCSA client + eligibility rules
│   │   ├── negotiation.py   # pure negotiation function
│   │   ├── attention.py     # rule registry for the attention queue
│   │   └── pitch.py         # server-controlled pitch_summary string
│   ├── models.py            # Pydantic v2 request/response models
│   ├── schema.sql           # loads, call_events, fmcsa_cache
│   └── seed_loads.json
├── tests/
├── Dockerfile
├── docker-compose.yml
├── fly.toml
├── pyproject.toml
└── README.md
```
