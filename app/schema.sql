CREATE TABLE IF NOT EXISTS loads (
  load_id TEXT PRIMARY KEY,
  origin TEXT NOT NULL,
  destination TEXT NOT NULL,
  pickup_datetime TEXT NOT NULL,
  delivery_datetime TEXT NOT NULL,
  equipment_type TEXT NOT NULL,
  loadboard_rate REAL NOT NULL,
  miles INTEGER NOT NULL,
  weight INTEGER,
  commodity_type TEXT,
  num_of_pieces INTEGER,
  dimensions TEXT,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'available'
);

CREATE TABLE IF NOT EXISTS call_events (
  call_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_seconds INTEGER,
  mc_number TEXT,
  carrier_legal_name TEXT,
  carrier_eligible INTEGER,
  load_id TEXT,
  loadboard_rate REAL,
  final_price REAL,
  final_carrier_offer REAL,
  negotiation_rounds INTEGER,
  outcome TEXT,
  outcome_reasoning TEXT,
  ineligibility_reasons TEXT,
  sentiment TEXT,
  sentiment_reasoning TEXT,
  transfer_attempted INTEGER,
  transcript_url TEXT,
  recording_url TEXT,
  summary TEXT,
  raw_payload TEXT,
  received_at TEXT NOT NULL,
  extract_model TEXT,
  extract_input_tokens INTEGER,
  extract_output_tokens INTEGER,
  extract_reasoning_tokens INTEGER,
  extract_cached_input_tokens INTEGER,
  outcome_model TEXT,
  outcome_input_tokens INTEGER,
  outcome_output_tokens INTEGER,
  outcome_reasoning_tokens INTEGER,
  outcome_cached_input_tokens INTEGER,
  sentiment_model TEXT,
  sentiment_input_tokens INTEGER,
  sentiment_output_tokens INTEGER,
  sentiment_reasoning_tokens INTEGER,
  sentiment_cached_input_tokens INTEGER,
  FOREIGN KEY (load_id) REFERENCES loads(load_id)
);

CREATE INDEX IF NOT EXISTS idx_call_events_received_at ON call_events(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_events_outcome ON call_events(outcome);

CREATE TABLE IF NOT EXISTS fmcsa_cache (
  mc_number TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
