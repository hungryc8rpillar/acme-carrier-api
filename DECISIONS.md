# Engineering Decisions

Working notes captured during the build of the Acme Carrier Sales voice agent and operator dashboard. Documents the meaningful design decisions, the architectural tradeoffs, and the bugs caught during testing.

Organized by topic rather than chronologically. The decision log is intended as a reference — read the section that's relevant, ignore the rest.

---

## 0. Domain primer — what's actually happening in US freight brokerage

Captured upfront so the same vocabulary and economic model lands consistently across the rest of the document.

### 0.1 Who's in the room

Three parties matter in every freight transaction:

- **Shipper (customer):** A company with goods to move. P&G, Walmart, a furniture manufacturer, a small e-commerce brand. Doesn't own trucks. Pays someone to get goods from A to B.
- **Carrier (the trucker):** A trucking company. Could be a one-truck owner-operator, a 50-truck regional fleet, or a 5000-truck national carrier. Owns trucks, employs drivers, holds federal authority to haul freight (the **MC number** is exactly that — Motor Carrier authority issued by the FMCSA, the federal regulator). Has insurance, DOT compliance, driver hours-of-service tracking.
- **Broker (Acme in our scenario):** The middleman. Doesn't own trucks. Maintains relationships with shippers on one side and carriers on the other. When a shipper says "I need a load moved," the broker finds a carrier to do it, takes responsibility to the shipper that it'll arrive, and earns the spread between what the shipper pays them and what they pay the carrier.

### 0.2 The call we're modeling — inbound carrier call

A carrier calls Acme's carrier desk because they just delivered a load and now have an **empty truck**. Empty trucks bleed money — fuel, driver hours, depreciation continue accruing while no revenue comes in. Carriers urgently want their *next load*, ideally:
- Out of the same area they just delivered to (avoid driving empty miles, "deadhead")
- Heading toward home or another good freight market
- Picking up soon (their hours-of-service window is ticking)

So they call brokers they have relationships with, or browse online load boards (DAT, Truckstop), looking for posted loads. When they see Acme has a load they like, they call in.

The caller might be the owner-operator themselves, an in-house dispatcher, or an outsourced dispatcher who works for the carrier on commission. For our agent the conversation is identical regardless.

### 0.3 What's being negotiated

The **rate** — how much the broker (Acme) pays the carrier for the whole load. Not per mile, not per hour. "$2,450 to move this load Atlanta to Dallas."

Out of that, the carrier covers everything: fuel (~$1.00/mile on diesel at current prices), driver pay (~$0.50-0.70/mile), tolls, truck maintenance, insurance, depreciation. Real all-in costs are around $1.50/mile for a typical owner-operator. On our example Atlanta-Dallas 881-mile load, that's roughly $1,322 in costs against a $2,450 rate — leaving ~$1,128 gross before the carrier's own overhead and deadhead miles.

That margin is why a carrier might push back from $2,450 to $2,800. Different carriers have different cost structures, different home-base preferences, different driver availability. The "right rate" is genuinely subjective.

### 0.4 Why brokers have a ceiling (and why ours is 12%)

The shipper paid Acme some price for the load — typical broker gross margin is 12-18% of the shipper rate. So on a load where the shipper pays $3,200, Acme expects to keep ~$400-600 in margin, paying the carrier $2,600-2,800 ish. Below that the broker loses money on the load after their own overhead (sales staff, dispatchers, software, factoring fees, the carrier desk).

In our code: `floor = loadboard_rate, ceiling = loadboard_rate × 1.12`. The ceiling represents the broker's negotiation guardrail — pay the carrier above this and the load stops being profitable. Not a moral limit, an economic one.

### 0.5 How the negotiation logic maps to broker reality

The `evaluate_counter_offer` function has four decision branches, each modeling a real broker behavior:

| Branch | Reason code | Real broker behavior |
|---|---|---|
| Carrier offer ≤ posted rate | `parity_with_loadboard` | Quick yes. Rare on inbound but handled. |
| Carrier offer between posted and ceiling | `within_margin_band` | Quick yes. Speed matters — losing the carrier to a competing broker over $50 is worse than the $50. |
| Carrier offer above ceiling, rounds 1-2 | `above_ceiling_concede_half` | Counter at midpoint of our prior offer and the ceiling. Real brokers do this — "I can stretch a bit but not that far." Signals flexibility without giving away too much. |
| Carrier offer above ceiling, round 3 | `above_ceiling_max_rounds` | Polite reject. Calls that don't close by round 3 statistically don't close — every extra minute is operational cost. |

The 3-round cap, the 1.12 ceiling, the concede-half pattern — these are **encoded broker economics, not arbitrary tuning**. A brokerage with different margins changes their config; the logic stays the same.

### 0.6 Why making this a pure function matters to the buyer

In real brokerage operations:
- A **CFO** can audit any disputed decision and see exactly why the agent said what it said (replayable from logs)
- A **compliance officer** can prove the agent followed policy on every call
- A **sales director** can change the margin band from 1.12 to 1.15 in one config change and see the impact uniformly across all calls from that moment
- A **new brokerage onboarding** to the system just sets their own floor/ceiling/rounds — the negotiation engine is the same

This is the difference between "a chatbot that talks about freight" and "a piece of software a brokerage would actually run their floor on."

---

## 1. Product framing decisions

### 1.1 Buyer persona = Director of Carrier Sales at a mid-size brokerage (50–200 reps)

Naming the role, the company size, and the buyer's daily question ("what do I do today to book more loads at better margin?") is the spine of every other product decision. Generic "freight broker" framing is too loose — the decisions about what shows up on the dashboard, what the agent prioritizes, and where the v1.1 roadmap goes all flow from the specific operator persona.

### 1.2 Business case framed in dollars, not features

ROI math: ~$55k of overhead per rep per year just to filter the inbound funnel, plus three hidden cost categories (no-bookings on calls nobody answered, slow response losing carriers to competitors, margin leak from inexperienced reps over-paying). The dashboard's metric choices (margin vs. loadboard, win rate, cost per booked load) trace directly back to these three cost categories.

### 1.3 90-day success metrics declared explicitly

Time-to-quote, booking rate, margin vs. loadboard, rep utilization, cost per booked load. With targets and a commitment to revisit at 60 days. Defining success upfront is what separates a vendor from a feature demo.

### 1.4 Deliberate scope: what we are NOT building

v1.1 list: funnel chart, hour-of-day heatmap, rep-level breakdowns, predicted-next-booking, real Command Center editing, recording URL integration. Listed as designed-not-shipped, not forgotten.

---

## 2. Architectural decisions

### 2.1 Stateless negotiation evaluator

**The architectural choice:** `/negotiations/evaluate` is a pure stateless endpoint. No server-side session storage, no per-call state in the database, no in-memory cache of in-progress negotiations. Every request is fully self-contained: same inputs always produce the same output.

**Why we chose stateless over stateful:**

1. **Auditable by replay.** With a stateless evaluator, any past negotiation decision can be reproduced from logs alone — feed the same `{load_id, carrier_offer, round, our_last_offer}` back into the function and you get the same answer. A brokerage operations director auditing a disputed booking can trace exactly what the system decided and why. With server-side state, the decision becomes a function of history-that-no-longer-exists.

2. **Idempotent under retries.** HappyRobot's Webhook node retries on network failure. A stateful endpoint that increments a round counter or mutates session state on each call risks double-advancing the negotiation if a retry lands. A stateless pure function returns the same answer on retry. Zero risk.

3. **Trivially unit-testable.** `evaluate(load, offer, round, our_last_offer)` has no DB dependencies, no HTTP context, no fixtures. We have 13 negotiation tests covering the full decision table — they run in <50ms because there's nothing to set up or tear down.

4. **Horizontally scalable with zero coordination.** A brokerage at production scale (1000+ concurrent calls) routes requests across many backend instances. A stateful negotiation evaluator requires session affinity ("send all rounds of call X to the same machine") or a shared session store (Redis, etc.). Stateless removes both — any instance can answer any request.

5. **Easier to port and customer-deploy.** A customer's IT team rolling this out doesn't have to provision a session store or worry about backend failover during an in-progress call. The negotiation logic is essentially a calculator — drop it anywhere.

6. **Forces clean separation of concerns.** Server does math; agent does conversation. The agent's LLM context window naturally carries the in-call state (it can read its own prior turns to know what it just offered). The server stays a calculator. The two layers don't entangle.

**How we make it work in practice:** The client (the HappyRobot agent) carries the state in the request body — `our_last_offer` on rounds 2+. This is the same architectural pattern REST itself uses ("client carries state, server stays clean") and what JWT-based auth does for sessions. The state-information that round-2 evaluation depends on still exists — it just lives in the request, not on the server.

### 2.2 Negotiation is deterministic, not LLM-driven

The agent calls `/negotiations/evaluate` and receives both the decision *and* the suggested agent phrasing. The agent reads the phrase verbatim. Brokerage customers will not accept an LLM hallucinating rates. Auditable + customer-tunable + the agent never quotes a number it shouldn't.

### 2.3 Server-generated `pitch_summary` and `status_summary`

Load search and carrier verification both return a ready-to-read string. Agent reads them verbatim. Consistent phrasing across all loads/carriers; we can A/B the pitch wording server-side without re-prompting the agent; agent can't invent details about loads or invent reasons for ineligibility (legal liability).

### 2.4 Two writes per call — early booking event + final post-call event

`transfer_to_rep` tool writes `outcome: booked` to `/calls/events` mid-call. The post-call extraction + classify chain writes the final enriched event. Both are idempotent on `call_id`. A carrier who hangs up immediately after transfer still gets counted as booked. Defensive against the post-call webhook arriving late or being missed.

### 2.5 Tool-level message and hold music tuning per tool

`evaluate_counter_offer` runs with `Message: None` and `Hold music: None` because it's sub-100ms. `verify_carrier` gets an AI message + ring tones because FMCSA takes 1–3s. Conversational realism. Per HappyRobot's own docs, "for tools that execute quickly (under 1–2 seconds), consider setting hold music to None to avoid an abrupt audio clip."

### 2.6 Backend in Python/FastAPI, dashboard in Next.js + TypeScript

Backend in Python (FastAPI) for build speed in a time-constrained window: deployable in under an hour, typed via Pydantic, testable via pytest. Dashboard in Next.js + TypeScript matching the team's primary stack. For a production deployment at scale, the backend would port to Node + TypeScript to match team tooling; the API contract and Pydantic models translate 1:1 to TypeScript interfaces and Zod schemas.

### 2.7 Two separate Fly apps, two separate repos

`acme-carrier-api` and `acme-carrier-dashboard` deploy independently. Two repos, two `fly.toml` files, two Fly apps, two URLs, one shared API contract over HTTPS with API key auth.

**Why this is the right shape, not just a convenience:**

- **Separation of concerns at the org level, not just at the file level.** A real carrier brokerage running this in production would have separate teams own the API and the dashboard. They deploy on different cadences. The backend goes through more review (FMCSA dependency, negotiation logic affects revenue, schema changes need migration discipline). The dashboard iterates faster (UX tweaks, KPI tile experimentation, new attention card types as data grows). Two repos make this natural; a monorepo forces every dashboard tweak through backend review.

- **Mirrors how a real customer pilot would deploy.** Backend on customer cloud (data sovereignty), dashboard on ours (we operate the UX), or vice versa. Either configuration is one-line to express when each is its own Fly app. In a monorepo it's a structural decision baked in from day one.

- **Independent deployments, independent CI cycles, separate logs/metrics per service** — this is how production systems are run.

**Trade-offs accepted:** two repos to navigate, two READMEs to maintain, two deploy commands. All cheap relative to the architectural benefits.

### 2.8 SQLite, not Postgres

Single file on a Fly volume. No ORM. Right-sized for a single-tenant pilot. Survives restarts. For multi-tenant production, would swap to Postgres. Schema is portable, queries are standard, data layer is isolated behind one module.

### 2.9 FMCSA reliability layer (cache + retry, never silent)

24h TTL cache in a dedicated SQLite table (`fmcsa_cache`). Cold-cache calls hit a single retry on 4xx with 600ms backoff — FMCSA's WAF flakes on the first request after idle and recovers within a second. **Failed lookups are not cached**, so a transient 403 doesn't poison the result for 24 hours. Real customers care about flaky upstream handling; this isn't `try: call(); except: return error`, it's a one-knob layer that hides FMCSA's bad days from the agent.

### 2.10 Stable machine-readable reason codes on every negotiation decision

Each negotiation response carries a `reason` enum-style code (`parity_with_loadboard`, `above_ceiling_concede_half`, `above_ceiling_max_rounds`, etc.) alongside the human-readable `agent_response_hint`. The dashboard can filter and group on reason without parsing English.

Auditability isn't just "we logged it" — it's "you can query it." The Director can ask "which lanes lost on `above_ceiling_max_rounds` this week?" with a SQL WHERE clause.

### 2.11 `/attention` is a rule registry, not a hardcoded set

Each card type is a pure function in `app/services/attention.py`; a module-level list registers them; `/attention` runs all rules and concatenates. Adding a 7th rule is one function and one list entry. Telegraphs the extensibility a Director would ask about — "can my analyst add a new card?" Yes, in 20 lines.

### 2.12 Negotiation evaluator has zero framework dependencies

`evaluate()` takes plain Python args and returns a dataclass — no FastAPI, no SQLite, no HTTP. The router is a 15-line wrapper. Porting to Node is mechanical (the function is ~80 lines).

---

## 3. The dashboard is a tool, not a chart wall

### 3.1 Three sections in priority order

Today's scoreboard → Needs Attention queue → Call log. The argument we're making about what's important is encoded in the ordering. We answer "what should I do today?" not "here's some data."

### 3.2 The Needs Attention queue is the headline feature

Six card types designed; two shipped for v1 (`floor_too_high`, `sentiment_negative_booking`); other four listed as v1.1. Demonstrates the loop is two-way. Agent does work → dashboard surfaces issues → human acts. This is what makes it a product, not a report.

### 3.3 KPIs are all dollar-or-decision-shaped

Loads booked, revenue, margin vs. loadboard, win rate, avg negotiation rounds, sentiment mix. Each maps to a different lever the Director controls. Picked deliberately, not gathered. Drop any one and you lose a lever.

### 3.4 No chart library

Inline SVG sparklines, HTML stacked bars. No Recharts, no Chart.js. Adding a chart library is a 30-minute trap; the visuals we need fit in 50 lines of SVG. Less is more in operations dashboards.

### 3.5 Dashboard auth = single API key, stored in localStorage

Sufficient for a pilot; production would use SSO with role-based access.

---

## 4. Operational catches

The bugs and gotchas caught during integration testing — kept here as concrete examples of the kind of issues that surface only when real systems meet real data.

### 4.1 Fly.io region switch: `fra` → `iad`

Frankfurt's egress IP is on FMCSA's WAF blocklist. Discovered during integration testing. Switched to Ashburn. Exactly the kind of integration gotcha a real customer cares about hearing you handled.

### 4.2 SQLite ephemeral path bug, caught and fixed

DB was writing to `/app/data/app.db` (container-ephemeral) instead of `/data/app.db` (mounted volume). Every restart wiped the DB and the FMCSA cache. Fixed via `DB_PATH=/data/app.db` env in `fly.toml`. Caught because cached MC lookups kept flipping between hit and miss.

### 4.3 Auth scheme properly declared in OpenAPI

`X-API-Key` is declared via FastAPI's `APIKeyHeader` security scheme. OpenAPI consumers see the auth contract accurately, and Swagger UI's Authorize button works natively (lock icon on every protected endpoint). Many APIs get this wrong; we got it right.

### 4.4 Defensive normalization at every string boundary (a design principle, not a one-off)

Same defensive pattern applied at three places where the API meets human or AI-generated text:

1. **Equipment type** (carrier says "van", loadboard has "Dry Van"): alias map in `app/routers/loads.py`.
2. **MC number** ("MC 123456", "mc-123456", "00123456" → "123456"): `normalize_mc` in `fmcsa.py`.
3. **Outcome / sentiment** (`Booking_Confirmed → booked`, `Happy → positive`, `Hung_Up → abandoned`): aliases in `models.py`.

Pattern: lowercase + strip + alias-map + safe fallback. Unknown values fall back to `other` / `neutral` instead of 422.

### 4.5 `DEMO_INELIGIBLE_MCS` env override for the ineligible flow

All seeded MCs happen to be eligible in the real FMCSA registry. Built an env-gated override (`DEMO_INELIGIBLE_MCS=400003`) that flips one MC to ineligible **before** rule evaluation, so the response is coherent end-to-end: `operating_authority_status: "INACTIVE"`, `ineligibility_reasons: ["operating_authority_inactive"]`, status_summary "Operating authority is currently inactive — we won't be able to book you on a load today." Legal name preserved from real FMCSA lookup. The override is auditable (visible in fly config), documented as a deliberate affordance, and empty in real production deployments.

### 4.6 `raw_payload` audit column on every call event

Every webhook body is stored verbatim in `call_events.raw_payload` (JSON) alongside the parsed columns. The dashboard call-detail endpoint returns it so anyone debugging can see "what the agent actually sent" — not just what we cleaned up. Forensic-grade auditability. If a booking is disputed three weeks later, we can replay the exact payload that came in.

### 4.7 Rate limiting on the API (60/min per key)

slowapi-based, keyed by `X-API-Key` (falls back to IP if missing). Single-tenant today but the key-scoped design future-proofs for multi-tenant — one noisy customer can't DoS another.

### 4.8 API key format — random URL-safe base64, no prefix

Keys are `secrets.token_urlsafe(32)` — 256 bits of entropy, ~43 chars, URL-safe characters only. No prefix.

**Considered and deferred:** Stripe/OpenAI-style prefix (`acme_live_<base64>` / `acme_test_<base64>`) to distinguish environments at a glance. Real value emerges only at multi-environment scale and after registering the prefix with GitHub's secret scanner. Single-tenant pilot doesn't need it.

### 4.9 Test pyramid — quantified

69 tests across multiple files, all passing, Ruff clean:
- `test_negotiation.py` — one test per decision-table branch + a non-linear 3-round sequence
- `test_fmcsa.py` — eligible, inactive authority, OOS, no-insurance, not-found, cache-hit, cache-TTL-expiry, MC normalization
- `test_calls_idempotency.py` — happy POST, replay, double-count check, full detail, 404, missing key, alias normalization, unknown-value fallback

Numbers beat claims. The negotiation tests in particular demonstrate the decision table isn't aspirational — it's enforced.

### 4.10 Seed load dates — rolling, computed at startup

**The issue:** originally, seed loads had fixed `pickup_datetime` values. As time passed (even 24 hours), the dates became stale relative to "now," and `search_loads` returned no matches for reasonable date queries.

**The fix:** on every container startup, compute the offset between current UTC and a fixed reference date (2026-05-14, the date seeds were authored), then shift every load's pickup/delivery datetime by that offset before inserting. Re-seed the `loads` table on every startup; leave `call_events`, `fmcsa_cache`, etc. untouched. The JSON template stays human-readable; the rolling logic lives in the seeding code.

**Why this is a feature, not a workaround:** mirrors how real load boards (DAT, Truckstop) maintain live load offers — postings are time-relative, not absolute. A load posted "available tomorrow" stays "tomorrow" forever in human terms; behind the scenes the system shifts datetimes.

### 4.11 Carrier vocabulary normalization — split between LLM and API

**Issue discovered during testing:** Agent sent `destination: "Texas"` (state name, as carriers naturally speak) to `/loads/search`. Our seed loads store destinations as `"Dallas, TX"` (state abbreviation). Substring matching missed — `"Texas"` doesn't appear in `"Dallas, TX"`. Result: `count: 0` despite a perfect logical match on everything else.

**Two distinct normalization problems, each handled where it belongs:**

| Problem | Handled by | Why |
|---|---|---|
| **Convention** (Texas ↔ TX, Atlanta ↔ Atlanta, GA) | API code, deterministic state-name expansion | Closed set (50 states + DC), same answer every time, auditable, free of LLM variability |
| **Semantic** (Lone Star State → Texas, DFW → Dallas, "down south", non-English references) | LLM via system prompt rule | Open-ended, requires world knowledge, LLM strength |

**Why this split is the right architecture:**
- The LLM does what LLMs do well: open-ended language interpretation.
- The API does what code does well: deterministic, predictable, auditable matching.
- Architecture is multilingual-ready: if Acme expanded to a Spanish-speaking carrier desk, the LLM handles "Tejas → Texas" naturally; the API doesn't need to change.

---

## 5. The HappyRobot platform — configuration decisions

### 5.1 Folder structure — `Acme Carrier Desk / Inbound Carrier Call`

Top-level folder named for the *function in the brokerage*, single workflow named for the *call type*. Pattern: folder = the seat, workflow = the call. "Carrier desk" is industry-native — what reps actually call the inbound seat.

### 5.2 Trigger — Web Call

Web Call trigger only. Production deployment would swap to a real `Inbound to number` trigger pointed at the brokerage's existing carrier-desk number.

### 5.3 Voice — Josh HR (American male, ElevenLabs)

**Why this voice for freight carrier sales:**
- Male voice fits the demographic — carrier sales desks at most US brokerages skew male; the role is high-volume, fast-paced, blue-collar-adjacent.
- American accent — FMCSA-regulated freight is a US-only market; any non-American accent breaks the immersion in 5 seconds.
- Age tone — Josh sounds late-20s to early-30s, matching the "second-year rep on the desk" persona written into the system prompt.

For production this would be customer-configurable — some brokerages would deliberately pick a female voice for differentiation; some might want regional accents.

### 5.4 Transcription keyterms — freight-specific vocabulary

Words pinned for the transcriber to prevent common mishears. Categorized:

**Regulatory / identifiers:** MC, FMCSA, DOT
**Equipment types:** dry van, reefer, flatbed, step deck, power only
**Operational terms:** drop and hook, no-touch, deadhead, TWIC, detention, layover, fuel advance
**Operational roles:** broker, dispatcher, loadboard, lane, backhaul

A carrier might call and say "I'm running reefers out of Miami, looking for a backhaul to the southeast" — without keyterms, the transcriber could mangle this into "running referrals" and the agent loses the entire intent.

### 5.5 Background ambient audio — off for the demo

Available options: Call center, Coffee shop, Office, Reception. For a clean recording or initial pilot evaluation, off is the right setting. For production this would be on — almost certainly `Call center` — to match the realism of an actual carrier desk.

### 5.6 Recording disclaimer — production compliance note

US call recording laws split between one-party-consent and all-party-consent states. **Eleven states require all-party consent for call recording:** CA, CT, DE, FL, IL, MD, MA, MT, NV, NH, OR, PA, WA. Production deployment must enable the disclaimer for any state in that list. The platform's "Recording disclaimer" dropdown handles this with one-click compliance per state.

### 5.7 Model selection — GPT-5.2 Instant for production, with provider-agnostic architecture

**Final state:** the voice agent runs on GPT-5.2 Instant (`gpt-5.2-chat-latest`). Instant variant (non-reasoning) means no reasoning overhead → comparable or better voice latency vs. Sonnet-class models. Tool-calling reliability is on par with Sonnet at the GPT-5.x tier. Instruction-following on verbatim-phrasing and never-quote-an-unauthorized-price rules has held up under voice testing.

**Why this matters architecturally:** the build initially ran on Claude Sonnet 4.6 and swapped to GPT-5.2 Instant mid-build when intermittent availability issues surfaced on the upstream side. The swap took ten minutes because the prompt and tools are provider-agnostic. Same prompt structure, same four tools, same negotiation logic, same post-call extraction chain. Only the model identifier changed.

**For production:** either Anthropic (Claude Sonnet-class) or OpenAI (GPT-5.2 Instant) is appropriate for this task; the architecture is provider-agnostic at the model layer. A two-provider fallback is the natural v1.1 — same prompt, route to whichever provider is healthy. Swap-resilience is what keeps a real carrier desk running when one model provider has a bad five minutes.

**Why not Haiku-class:** Latency win is real (~690ms) but tool-calling reliability and complex instruction-following are where smaller models stumble. Four tools, conditional negotiation logic, verbatim phrasing rules — too much risk.

### 5.8 Prompt iteration discipline — text-mode first, voice second

Used HappyRobot's Chat Playground heavily for prompt iteration before any voice testing. Order matters:

1. **Prompt Issues (static analysis):** click after writing the prompt; surfaces contradictions, vague instructions, missing edge cases.
2. **Chat Playground (text-mode iteration):** run all three test scenarios (happy path, ineligible, no match) in text. Tool calls still execute. Faster than voice — no transcription delay, no playback time.
3. **First voice call test:** Once text is clean, run as voice. Specifically listen for verbatim reading of `pitch_summary` and `agent_response_hint`, awkward pauses, natural speech vs. AI-assistant tone.
4. **Tune on voice findings:** voice surfaces different failures than text — phrasing that reads fine often sounds wrong out loud.

### 5.9 Current-date context — required prompt injection

**Issue discovered during first text-mode test:** Agent translated carrier's "Friday" into `pickup_date: 2025-05-16` — exactly one year in the past. The LLM was inferring "now" from training-data norms (2025-era) rather than actual current date.

**Fix:** Added a `# Current context` section at the very top of the system prompt (above `# Role`), using HappyRobot's built-in current-time variable. Time zone: **America/New_York (Eastern)** — chosen because (a) Acme is a US freight broker, (b) most major US freight hubs operate in Eastern/Central, and (c) Eastern is the most common HQ timezone for the industry. Don't use UTC for voice agents; users reason in human time.

**Placement rationale (a generalizable principle):** the block goes above `# Role` because date is *context* — a fact the LLM needs in order to interpret everything below — not a behavior rule. Standard prompt structure: **facts first, role second, rules third, edge cases last.** Each layer depends on the ones above it.

### 5.10 Prompt examples — structural templates, not concrete scripts

**Lesson:** first version of step 7's confirmation example had a concrete sample sentence ("So we're locked in at twenty-six-twenty-five for L-D ten oh one, Atlanta to Dallas"). Risk: LLM pattern-matches too literally and says "Atlanta to Dallas" on a call that was actually Miami to Orlando.

**Fix:** Step 7's example is now a structural template using bracket placeholders: `"So we're locked in at [price] for [load ID], [origin] to [destination]."` The `[bracket]` convention is universally recognized by LLMs as "substitute, don't copy."

**Generalizable principle:**
- **Concrete examples** belong where teaching *style or tone* (e.g., `# Voice rendering` uses "twenty-four-fifty" not "$2,450" because we're showing natural speech).
- **Structural templates with placeholders** belong where teaching *shape of response* (e.g., step 7's confirmation pattern).

Mixing them risks pattern-match leakage — the LLM might copy concrete content from an example meant only to demonstrate structure.

### 5.11 Tool message strategy — fixed phrasing vs. AI-generated

Each custom tool in HappyRobot has three options for what the caller hears while the tool executes: AI-generated, Fixed, or None. Per-tool decisions:

- `verify_carrier`: **AI mode** with example anchor. Tool is slow (1-3s FMCSA round-trip) and the *next* utterance from the agent is short. AI filler does real work here.
- `search_loads`: **Fixed** ("One moment, pulling up matching loads."). Tool is fast (100-300ms SQLite query) and the *next* utterance is rich (the personalized `pitch_summary` from the API). AI filler before a personalized pitch is double-work.
- `evaluate_counter_offer`: **None** — sub-100ms tool, no time for any audio, an awkward "let me think..." would break the negotiation rhythm.
- `transfer_to_rep`: **Fixed**, with deliberately natural broker phrasing — `Perfect — connecting you to a rep to lock this in.` Freight-idiomatic ("locking in" a rate/booking is real broker phrasing).

**Generalizable principle:**
> Tool message strategy should match the latency profile of the tool AND the information density of what follows. Slow tool + short follow-up → AI filler does real work. Fast tool + rich follow-up → Fixed is correct (don't double-personalize). Sub-second tool → None (any audio is awkward).

### 5.12 Webhook child node configuration — auth and error handling

Three non-obvious config decisions per child Webhook node:

**Authentication: `API Key` dropdown, not custom header.** Both approaches technically send the same HTTP header, but the dropdown is semantically correct (the workflow declares it does API Key auth) and reads as platform fluency.

**Error handling: enable "Gracefully handle 5XX errors" on every Webhook child.** Without the flag, a 5XX from our API (FMCSA timeout, Fly cold-start, transient crash) causes a hard tool failure that the agent can't recover from cleanly. With the flag, the tool returns a structured failure that the agent reads and responds to conversationally.

**Preserve data types on POST bodies: enabled.** Without it, HappyRobot serializes all body fields as JSON strings — `"carrier_offer": "2700"` instead of `"carrier_offer": 2700`.

### 5.13 Post-call processing chain — Extract → Classify Outcome → Classify Sentiment → Webhook POST

Four-node chain that runs after the agent ends. Produces the enriched call event that UPSERTs onto the partial row `transfer_to_rep` may have written mid-call.

**Why four nodes and not one Extract with everything in it.** Each node has a different input shape it wants (Extract wants the transcript; Classify Outcome can run off Extract's summary; Sentiment needs the transcript for tone), a different output contract (Extract = many free-form fields; Classify = exactly one enum value with platform-enforced constraint), and a different reason to be tuned independently (swapping the sentiment model shouldn't require re-running fact extraction). Separation of concerns at the chain level mirrors separation of concerns at the API level.

**Outcome categories (7):** `booked`, `declined_price`, `declined_no_match`, `ineligible_carrier`, `abandoned`, `escalated`, `other`.

**Sentiment categories (4) — and why the `negative` vs `frustrated` split is intentional:**

- `positive` — friendly, agreed easily, would call back.
- `neutral` — transactional and flat. Most calls should land here.
- `negative` — unhappy with the *outcome*. Didn't get the price/lane they wanted, said so, ended the call civilly.
- `frustrated` — annoyed with the *interaction itself*. Raised tone, interruptions, sarcasm. Distinct from negative.

The split between `negative` and `frustrated` is the operationally important one. It powers the `sentiment_negative_booking` card in the Needs Attention queue (`outcome=booked AND sentiment=frustrated` — "we got the deal but the carrier left the call angry, listen to this one"). This is a *retention* signal that the booked/declined dichotomy cannot surface.

Collapsing to three categories erases this signal entirely. Going to five would add noise without adding a *decision* the operator would make differently. Four categories, three of which map to a concrete dashboard or operational decision, is the right granularity.

### 5.14 `transfer_attempted` is independent of `agreed_price` — necessary but not sufficient

The relationship is one-directional:

- `transfer_attempted = true` ⟹ `agreed_price` exists (transfer is necessary, because the tool requires it as a parameter)
- `agreed_price` exists ⇏ `transfer_attempted = true` (price agreement is NOT sufficient — the agent may agree and then fail to call transfer_to_rep)

**Realistic failure modes between price agreement and transfer:**

- Agent agrees on price, hallucinates that the call is over, hangs up
- Agent agrees on price, asks a follow-up question, carrier hangs up before transfer
- Agent agrees on price, gets confused by something the carrier says next, goes off-script

In all cases, `evaluate_counter_offer` returned `decision: "accept"` and `agreed_price` is populated, but `transfer_to_rep` was never called. These "agreed but no transfer" calls are exactly the calls a Director would want flagged — they surface as `outcome=other` or `outcome=escalated` in the dashboard.

**Generalizable principle:** when two events are sequentially wired in an LLM workflow but executed by an LLM, do not collapse them into one boolean. The "necessary but not sufficient" framing keeps the signal honest and surfaces the cases where the LLM didn't follow its instructions.

### 5.15 Classify reasoning fields promoted to first-class data

Both Classify nodes (outcome and sentiment) expose two outputs each — the category AND the model's reasoning. The reasoning was originally treated as a byproduct; promoted to first-class data and added `outcome_reasoning` and `sentiment_reasoning` to the call event.

When an operator clicks a row and sees `outcome=other` or `sentiment=frustrated`, the next question is always "why?" Without reasoning fields, that's a 2-minute transcript skim. With reasoning fields, it's a 2-second glance at:

> *"Polite negotiation throughout, accepted counteroffer without complaint, ended call with 'Awesome' — friendly, engaged, clearly satisfied tone."*

That's an actual reasoning field from a live test call (sentiment=positive). Operational, not decorative.

**Generalizable principle:** when an LLM classifier offers reasoning as a byproduct, capture it. The chain-of-thought is genuine signal — disposing of it loses information for free.

### 5.16 Idempotent SQLite migrations via `PRAGMA table_info`

SQLite has no Alembic equivalent. Rolled our own idempotent migration:

```python
def _migrate_call_events(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(call_events)")}
    if "final_carrier_offer" not in cols:
        conn.execute("ALTER TABLE call_events ADD COLUMN final_carrier_offer REAL")
```

Called from `init_db()` after schema executescript. On fresh DBs, the column is already present from `CREATE TABLE` (no-op). On existing DBs, the ALTER runs once and adds the column without touching existing rows. For multi-tenant production we'd want Alembic; v1.1 item.

---

## 6. The boundary-type bug class (and the principle that emerged)

Same class of bug appeared seven times during integration testing: empty strings or unexpected types reaching Pydantic from the LLM-driven enrichment chain. Each individual bug is logged below, but the more valuable artifact is the **principle that emerged from recognizing the class**: every nullable type at an LLM-driven API boundary needs the same defensive shape.

### 6.1 The seven bugs

| # | Bug | Symptom | Root cause | Fix |
|---|---|---|---|---|
| 1 | `mc_number` as int | string validation 422 | Pydantic type | Coerce int → str |
| 2 | `started_at` missing | required field 422 on partial writes | Pydantic required | Optional + default_factory |
| 3 | `our_last_offer` as `""` | float parse 422 on round-1 negotiation | Pydantic numeric | `""` → None coercion |
| 4 | `our_last_offer` as `"0"` (transient) | observed once, not reproduced | Pattern-completion non-determinism | deferred (no recurrence) |
| 5 | `CallEvent` numerics as `""` | 422 on ineligible post-call enrichment | Pydantic numeric | `""` → None coercion |
| 6 | `ended_at: 1` | 500 SQL/serialization | Datetime + storage | Remove from POST; derive on read |
| 7 | `load_id` as `""` | 500 FK violation | **SQL layer**, post-validation | `""` → None in validator |

### 6.2 The principle (Postel's law for LLM-driven clients)

Be very lenient in what the API accepts:
- Empty strings → None for any nullable field
- Numeric strings → properly typed value
- Common variant strings (`"null"`, `"none"`, `"true"`, etc.) → properly typed value or None
- Type variations (int where string expected, string where int expected) → coerce

Be strict in what gets stored:
- Normalized via internal helpers (`_normalize_mc`, etc.)
- Validated against business rules (gt=0 on prices, enum membership on outcomes)
- Foreign key integrity at the SQL layer

The validators apply uniformly across every nullable field at every LLM-driven API boundary. Adding a new optional field to a model is implicitly a request for the same defensive treatment.

### 6.3 What this enables architecturally — decoupled failure domains

In a voice-agent system, the carrier's experience on the call and the operator's experience in the dashboard are **two separate concerns with separate failure modes**. The architecture deliberately keeps them decoupled.

- **Agent-side robustness** is handled at the tool-call layer: every Webhook child node has "Gracefully handle 5XX errors" ON, the prompt has fallback handling for verification/search failures, the agent can recover conversationally from any backend transient. The carrier never hears silence, weird ad-libs, or abrupt hangups.

- **Dashboard-side robustness** is handled at the data-pipeline layer: defensive coercion at every LLM-driven API boundary, idempotent UPSERTs on `call_id`, the "two writes per call" partial-then-enriched pattern so we never lose data even when the post-call chain fails.

**Why decoupling matters:** these two failure modes can occur independently. A carrier can have a flawless call while the post-call enrichment fails. The carrier never knows. The dashboard shows missing data; we recover that on the next call. Conflating them — say, by failing the call if the dashboard write fails — would create a system where dashboard issues degrade the customer experience for no operational reason.

The boundary-type bugs proved the architecture worked as designed. The bugs fired in production on real voice calls. The carrier had a textbook experience: polite verification, clear status summary, callback offer, clean close. Meanwhile, the data pipeline failed loudly in HappyRobot's Runs panel — a 422 visible in logs, not a silent dropped event. We caught it in development, not in front of a customer. The fix was a one-file change to a known pattern.

---

## 7. Negotiation noise injection — deterministic per load

### 7.1 The problem

The negotiation decision table is deterministic. A repeat carrier who calls multiple times on the same lane could detect the pattern: "agent's first counter is always exactly halfway between loadboard and ceiling," "ceiling is always exactly 12% above posted rate." Pattern detection lets a sophisticated carrier game the quoting engine.

### 7.2 The v1 fix shipped

Inject deterministic noise into the ceiling multiplier, keyed on `load_id`. Range: ±5-15% on the 12% ceiling (effective ceiling varies between 1.07× and 1.18× of loadboard, per load). Computed as a hash of `load_id` mod the noise band.

The noise is **deterministic per load** — replays of the same load give the same answer, so auditability and unit tests still work. Different loads get different effective ceilings.

### 7.3 Honest attacker-model analysis

Three carrier-attack patterns exist. The current design defeats one cleanly:

| Attack pattern | Current design defeats? | Why |
|---|---|---|
| Cross-lane pattern inference ("all loads have ceiling = 12% above loadboard") | ✅ Yes | Per-load noise breaks the constant assumption |
| Same carrier, repeat calls on the same load_id | ❌ **No** | Same load → same noise key → same ceiling. Carrier calling LD-1042 on Monday and again on Tuesday sees the identical ceiling. They learn it in one or two calls. |
| Coordinated multi-carrier inference (broker-network sharing) | ⚠️ Partial | Different loads still vary; same load shared between carriers reveals the same value. |

### 7.4 v1.1 plan

Key the noise on `(load_id, current_date)` — same auditability (the date is in the call log alongside `load_id`), real anti-gaming (carrier calling tomorrow on the same load gets a different ceiling). The stronger version is `(load_id, date, carrier_mc)` — defeats coordinated multi-carrier inference too.

**Why not ship v1.1 keys in v1:** for a pilot evaluation, ceiling stability lets the operator reason about quotes without having to track date and carrier as variables. Stability is a feature for the pilot phase. Once trust is established, gaming-resistance becomes the more valuable property — a 10-line change.

### 7.5 Why truly random would be wrong

Breaks reproducibility in unit tests, breaks operator trust ("why did this carrier get a different number than that carrier on the same call?"), breaks audit ("why did we quote $X to this carrier?"). The right framing is "keyed pseudo-random with stronger keys," not "actually random." In production pricing engineering, every quote must be reconstructible from logs.

---

## 8. Cost telemetry — real where possible, constants where not

### 8.1 The architecture

Three post-call AI nodes (Extract, Outcome Classifier, Sentiment Classifier) expose token usage cleanly as HappyRobot workflow variables. We capture all six telemetry fields per node (model + input + output + reasoning + cached_input + uncached_input), ship them on the existing `/calls/events` webhook, and compute real per-call cost from published model pricing.

The voice agent node does NOT expose token usage as a workflow variable. We compute the voice agent's per-call cost from a documented constant ($0.085/call, dominated by TTS at ~88% of cost) until the platform exposes the data.

### 8.2 Why the gap

Confirmed with HappyRobot's platform copilot: the voice agent / Prompt node doesn't currently surface `Llm Usage *` variables the way the post-call nodes do. This is a real product gap — the voice agent is the highest-token-volume node in any voice workflow, so excluding it from native cost tracking forces customers into one of two workarounds:

1. API-level pull from session/run details (~1.5-2h of integration work, plus timing/race-condition handling between webhook arrival and run finalization)
2. Estimate from `num_total_turns` × constants (defeats the point of real telemetry)

For v1 we picked (2). The dashboard's AI cost tile has a methodology footnote that says exactly that.

### 8.3 The constant breakdown

For a 90-second voice agent call:

| Component | Per call | Why |
|---|---|---|
| TTS (ElevenLabs Josh HR) | ~$0.075 | $0.05/min × 1.5 min |
| STT (speech-to-text) | ~$0.009 | $0.006/min × 1.5 min |
| Voice agent LLM tokens | ~$0.005 | ~14K input + ~600 output tokens × ~6-8 turns |
| **Voice agent total** | **~$0.089** | rounded to $0.085 constant |

The dominant cost is voice synthesis (~88% of per-call), not LLM tokens. True of basically every voice agent in production — voice processing eats LLM costs for breakfast at current token pricing.

### 8.4 Cached input tokens kept in the schema — small forward-precision investment

For all three of our post-call AI nodes, cached input is currently zero because prompts vary per call. Briefly considered dropping `cached_input_tokens` from the payload for simplicity.

**Decision: keep it.** v1.1 will almost certainly add a shared system-prompt prefix for tone consistency or a shared rubric for the classifiers — and the moment that happens, OpenAI's automatic prompt caching will trigger on those prefix tokens. The rates diverge (cached input is ~10× cheaper than uncached). Capturing it now is essentially free; not capturing it means a migration later.

**Generalizable principle:** the right question for "should we capture field X" isn't "do we use it today?" — it's "what's the cost of not having it when we need it?" For cheap fields where the v1.1 use case is foreseeable, capture-now is the lower-total-cost option.

### 8.5 Single-endpoint architecture for cost data

Considered putting cost telemetry on a separate webhook → separate backend endpoint for "cleaner separation of concerns" between operational call data and AI cost telemetry.

**Decision: keep everything on the single existing `POST /calls/events` webhook.** One logical event (a call happened, here's everything we know about it), one transport, one row in `call_events`, one idempotency story keyed on `call_id`.

**When a separate webhook would have been right:** different consumers, different retry semantics, different security boundaries, independent time sources. None of those applied here — same consumer (dashboard), same retry semantics, same security boundary, same time source.

**Generalizable architecture principle:** field count growth on a single API endpoint is not, by itself, a reason to split the endpoint. The right question is whether the fields serve different consumers, evolve on different cadences, or have different operational properties. If none of those, keeping them together is the right default — splitting prematurely is the more expensive mistake.

---

## 9. Two findings from the operational layer

### 9.1 "Idempotent by ID" breaks when the schema changes

The seed script was idempotent on `call_id` (skip rows that already exist). This worked fine for re-running against an unchanged schema. When we added new token columns, re-running the seed left those columns NULL on existing seed rows because the script's "this row exists, skip" check short-circuited the new fields.

**Fix:** updated the seed script to be **self-resetting for `seed-*` prefixed rows** — wipes and re-inserts them on every run; non-`seed-*` rows (real call UUIDs) are untouched. The prefix scopes destruction to demo data.

**Generalizable principle:** idempotency by primary key is *not* the same as idempotency by content. When seed data needs to evolve (new columns, corrected values, redistributed dates), an ID-keyed "skip if exists" check becomes the bug. For demo seeds specifically — where the data is meant to track schema changes and reseeding is a regular operation — **content-replacing idempotency** (delete-by-prefix then re-insert) is the right default. The prefix is the safety boundary that prevents accidentally touching real data.

### 9.2 Operational findings on the deployed image

Two reusable facts surfaced during ad-hoc DB operations:

- **Prod backend DB path is `/data/app.db`**. Running a different path verbatim would silently create an empty file on the volume and report `deleted=0` — success-looking output that masks a no-op.
- **`sqlite3` CLI is not in the deployed image** (`python:3.11-slim` base). Ad-hoc SQL has to go through inline Python (`python -c "import sqlite3; ..."`) or a helper script.

Worth knowing for any Fly-deployed system: `flyctl ssh console` (which can flake on cert mint) and `flyctl machine exec` are sibling capabilities with different reliability characteristics. SSH is more interactive (lands you in a shell); `machine exec` is more script-friendly (one-shot command). Knowing both means a cert flake never blocks a deploy.

---

## 10. Dashboard build — specific decisions worth documenting

### 10.1 shadcn/ui version pinned to 2.1.8, not @latest

`pnpm dlx shadcn@latest init` defaults to the new `base-nova` style with `oklch()` color values. This conflicts with our dashboard spec (new-york style, zinc base, HSL color tokens). Pinned to 2.1.8 (last version with new-york + zinc + HSL CSS variables default).

**Generalizable principle:** for any tool with active design-system evolution, `@latest` is a footgun. Someone cloning the repo months later will get whatever version is latest then, which may have moved on again. Pin explicitly.

### 10.2 API client returns typed result shape, not raw throws

`lib/api.ts`'s `request<T>` returns `ApiResult<T> = { ok: true, data: T } | { ok: false, error: string, status?: number }` rather than throwing on non-2XX. Every consuming component pattern-matches on `result.ok`.

Forces every caller to make the error-handling decision once at the call site, and the type system enforces it (can't access `result.data` without first checking `result.ok`). For 5 endpoints and a compressed build window, a 5-line type and a 27-line `useApi<T>` hook is enough. Gives us 80% of React Query's error semantics for 1% of the dependency surface.

### 10.3 Self-recovering API key gate via CustomEvent

When `request<T>` receives 401/403 from the backend, it removes the stored API key from localStorage AND dispatches a `acme:api-key-cleared` CustomEvent. `ApiKeyGate.tsx` listens for the event and re-renders the prompt.

Why CustomEvent over React Context: 4 lines total vs ~20 lines of provider boilerplate. Equivalent functionality.

Why this is a small product-polish moment: the gate isn't a one-shot prompt that disappears forever after first entry. If the backend rotates the API key, or if a key is revoked mid-session, the dashboard self-recovers.

### 10.4 Honest comparisons via multi-gate suppression

Initial implementation showed "vs 7-day avg" comparisons on every KPI tile whenever the timeseries had ≥3 prior data points. Against sparse data, this produced misleading comparisons like "+300% vs 7-day avg" on Loads Booked (3 calls today vs an average of ~0.75/day).

**The fix:** introduced `MetricKind` (`count` | `currency` | `rate`) with kind-specific suppression gates in `lib/compare.ts`:
- All kinds: require ≥3 prior data points
- All kinds: require `|delta| ≤ 200%` (deltas beyond that are math artifacts, not signals)
- `count` (loads_booked): requires prior avg ≥ 2
- `currency` (revenue): requires prior avg > 0
- `rate` (win_rate, avg_margin): requires prior avg ≠ 0

When suppressed, the sub-line doesn't render at all — the tile shows just the big number.

**Generalizable principle:** suppress confidently. A dashboard that says "I don't have enough history to compare yet" is more trustworthy than one that fabricates +300% deltas to look impressive. Gates should fire on *operational* meaningfulness, not just *mathematical* validity.

### 10.5 Page-level data fetching with prop drilling, not per-tile refetch

`app/page.tsx` is the single owner of all four dashboard data sources. Slices passed down as props. No component fetches its own data; no Context provider; no global state library.

Why over per-component fetching: with six KPI tiles each fetching their own slice of `/metrics/today`, the dashboard would issue six redundant requests per poll cycle. Page-level ownership = one request per source per poll.

Why over Context / Redux / Zustand: unnecessary abstraction for the scope. Five data sources, eight consumer components, all rendering in one page. Prop drilling at this depth is cleaner than introducing a state-management layer to avoid it.

### 10.6 Attention queue placed above KPI tiles — editorial, not technical

The Needs Attention queue sits above the Today/KPI grid, which sits above the Call log. Most consumer-grade dashboards put big numbers at the top because numbers are visually impressive. The Director of Carrier Sales doesn't open this dashboard to admire the revenue tally — they open it to ask "what needs my attention today?" Putting attention first answers their actual first question.

This is the dashboard's *argument*: the operator's job is to make decisions today, not to admire yesterday's outcomes. The information hierarchy makes that argument visually.

### 10.7 Generic `AttentionCard` driven by data shape, not card type

`components/AttentionCard.tsx` is a single component that renders any item from the `/attention` endpoint, driven entirely by the `AttentionItem` shape (`type, severity, title, description, related_call_ids, suggested_action`). No special-casing on `type`.

The alternative — a switch on `type` with a custom component per card kind — is what most developers write first. Works for two cards. Becomes a maintenance burden at six. The generic component pulls the rule-specific logic *to the backend* and keeps the dashboard a pure renderer. New rules become a backend-only change.

### 10.8 Exhaustive TypeScript unions enforced via `assertNever`

`lib/badge-colors.ts` exports `outcomeColor(o: Outcome)` and `sentimentColor(s: Sentiment)` functions. Each is a switch statement with a `default: return assertNever(o)` branch.

If a new outcome or sentiment value is added to the union in `lib/types.ts`, TypeScript immediately errors on the color map because the new value isn't matched and falls through to `assertNever`. The build fails until the color map is updated.

This is the difference between "the code works" and "the code stays correct as the system grows." Without `assertNever`, a new backend outcome would silently fall through to a default gray and ship invisibly.

### 10.9 Paired-field rendering: both visible or both em-dashed

The "Loadboard / Final" column renders as `$2,450 / $2,575` when both fields are present, and as a single `—` when either field is null. Never as `$2,450 / —` or `— / $2,575`. The pair is treated as a unit.

Partial rendering looks broken even when it's technically correct. A booked call with `loadboard_rate: 2450` and `final_price: null` would render as `$2,450 / —`, which reads as "we have half the data" — except a viewer scanning the table doesn't know which half is missing or why.

**Generalizable rule:** when two fields are operationally linked, render them as a unit. Either both populated or both blank.

### 10.10 Color = meaning, never decoration

The dashboard uses four meaningful colors plus gray:
- Green for positive outcomes (booked, positive sentiment, info-severity)
- Red for action-needed states (ineligible, frustrated, critical-severity)
- Yellow for watch states (declined, warning-severity)
- Orange for negative-but-not-action-required (negative sentiment)
- Gray/zinc for neutral or inactive

Three colors plus gray is roughly the maximum the eye can hold simultaneously without confusion. Every additional color adds a category the viewer has to learn. Nothing is colored for visual interest.

### 10.11 Polling cadence: 30s for live data, never for static

`/metrics/today` and `/attention` poll every 30 seconds (live operational data). `/metrics/timeseries` and `/calls` do not poll (static-enough that one fetch suffices). The `useApi<T>(fetcher, pollMs?)` hook makes this a one-argument decision per consumer.

30s matches the cadence a real operator would refresh manually — fast enough to feel live, slow enough to not burn the API.

### 10.12 Seed data designed to exercise the attention rules, not just to populate

The seed script inserts deliberately-crafted call_events covering every attention-rule firing condition:
- 4× `booked` (positive, neutral, frustrated, zero-margin) → exercises sentiment mix and margin distribution
- 2× `declined_price` on the same load within 7 days → triggers `floor_too_high`
- 1× `booked + frustrated` → triggers `sentiment_negative_booking`
- 3× `ineligible_carrier` with distinct `ineligibility_reasons` codes → exercises structured-codes pattern
- 1× `declined_no_match`
- `received_at` spread across multiple days → multiple data points for the sparkline

Idempotency via `seed-*` `call_id` prefix: second run inserts 0 rows.

Seed data designed to exercise the *rules* (not just fill rows) means the dashboard's headline feature — the Needs Attention queue — has live, real-looking content from minute one.

---

## 11. The `floor_too_high` attention rule scope decision

The `sentiment_negative_booking` rule fires on `sentiment == 'frustrated'` only, not on `negative`.

**Rationale:**

The card's suggested action is "Have a rep call the carrier to check in before pickup." That's *operator labor* — every triggered card represents a rep dialing a number. Rep time is finite. The rule should fire only when a rep call has high information value.

- **Booked + Frustrated**: carrier was annoyed during the *interaction*. Real retention risk — they may switch brokers because of the friction. Rep call has high value (smooth the handoff, identify the friction).
- **Booked + Negative**: carrier was disappointed about the *outcome* (price). Got a price they didn't love but interaction was civil. Rep call is awkward and low-value — they wanted more money; we're not going to fix that on a check-in call.

**Principle:** attention queues degrade quickly if every rule fires too liberally. The rule scope reflects what an *operator can actually do about it*, not what we can *detect*. We could detect both negative and frustrated; we choose to surface only the one where surfacing changes outcomes. The attention queue is *not a notification feed.* Every card represents work the operator will do.

---

## 12. Sentiment classifier — observed positivity bias, calibration plan documented

### 12.1 The observation

During recording: International Trucking Corp call. Carrier performed deliberately *neutral* — flat tone, transactional language ("Alright. Send it over." / "Got it."), no warmth markers. Classifier returned `positive` with reasoning: *"Carrier negotiated professionally, accepted the agent's final offer and confirmed booking with no complaints—amicable and cooperative tone."*

The classifier's own reasoning describes neutrality, not positivity. By the sentiment-tag definitions in the prompt:
- `positive` is "friendly, agreed easily, sounded happy... no friction"
- `neutral` is "transactional. Professional but flat. No clear emotional tone either way."

The reasoning emitted ("professional", "no complaints", "amicable") fits the `neutral` definition almost verbatim. "Amicable" specifically means *absence of hostility*, not *presence of warmth*.

### 12.2 Diagnosed bias

The classifier appears to conflate **outcome success** (booking closed) plus **interaction civility** (no friction) with **positive sentiment**. In reality these three are orthogonal — a carrier can book successfully, behave civilly, AND still be neutral about it (the modal case for B2B sales calls).

### 12.3 v1.1 calibration plan

The right way to fix this is not a single prompt tweak. It's a calibration cycle:
1. Hand-label ~50 historical calls with what the classification *should* be (operator ground truth)
2. Measure agreement between current classifier output and the labels
3. If positive is over-called >20% of the time on what should be neutral, iterate on the prompt definitions with concrete examples ("amicable but flat → neutral", "expressed appreciation → positive")
4. Re-evaluate against a held-out set
5. Optionally fine-tune a smaller model on the labeled set if prompt-tuning hits a ceiling

This is the kind of detail a careful pilot would build into the customer relationship — the classifier is a v1 hypothesis, not a final answer. The data model already captures `outcome_reasoning` and `sentiment_reasoning` precisely so this calibration is *auditable*: every classification carries its own justification, which makes finding systematic biases tractable.

**Operator UX implication:** the "Sentiment mix" KPI tile and the call-log sentiment column should be read as relative-distribution indicators, not absolute truth. The dashboard exposes the model's reasoning on every call so disagreements are visible and the system improves explicitly.

---

## 13. Dashboard catches a workflow wiring bug

### 13.1 The finding

During the parity-rule test call (Arrol Scott Bullard, MC 1015734) on LD-1001: agent correctly fired the parity path. Pitched at $2,450, carrier accepted at $2,450 verbatim, `evaluate_counter_offer` was never called (parity rule short-circuits before negotiation), `transfer_to_rep` fired with `load_id: LD-1001` and `agreed_price: 2450` in its payload. Transcript shows all the right things happened.

What landed in the DB: `load_id: null`, `negotiation_rounds: null`, `final_carrier_offer: null`. Outcome correctly classified as `booked`, sentiment as `positive`. But the row was missing the load identifier — visible in the call log as an em-dash, broken provenance.

### 13.2 Root cause

The Webhook POST body had `load_id`, `negotiation_rounds`, and `final_carrier_offer` bound to `@evaluate_counter_offer.<field>` variables. Those variables resolve to whatever was passed *to* that tool's call — and on parity accepts, the tool was never called, so the variables resolved to null.

Structural mismatch between the workflow's wiring assumptions and the agent's actual behavior. The wiring assumed every booking goes through `evaluate_counter_offer` at least once. The parity rule (and any future ad-lib path) breaks that assumption.

### 13.3 The fix

Rebind those three fields to Extract's transcript-derived values:
- `load_id` → `@Extract Call Details.load_id`
- `final_carrier_offer` → `@Extract Call Details.final_carrier_offer`
- `negotiation_rounds` → `@Extract Call Details.negotiation_rounds`

`loadboard_rate` was already bound to the `search_loads` response (the API's return value, not a tool input), so it's unaffected. `mc_number`, `legal_name`, `outcome`, `sentiment`, `carrier_eligible`, `ineligibility_reasons`, and `summary` were already bound to Extract — only the negotiation-derived fields had the wrong binding.

### 13.4 Why this is significant

**The dashboard caught a workflow-wiring bug.** The row was technically valid (the optional field is nullable), the post-call webhook returned 201, no error fired anywhere in the stack. Without the dashboard rendering this anomaly visibly, the bug would have shipped silently.

The dashboard's role isn't just to report what happened — it's to surface anomalies that look like bugs even when nothing technically failed.

### 13.5 Generalizable rule

For any field that exists in multiple agent paths with different sources, bind to the universal source (transcript via Extract) rather than to a path-specific tool input. The transcript is the only artifact every call produces; binding to it makes the enrichment robust to every conversation path the agent might take.

Use tool-input variables only when (a) the field is set on *every* call regardless of path (e.g. `mc_number` from `verify_carrier`, which fires unconditionally) or (b) the determinism is operationally important (e.g. cryptographic signatures, exact numeric values that must round-trip without LLM mediation).

---

*Maintained alongside the codebase. Updated as decisions evolve.*
