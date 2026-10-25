-- Inactive Codex/Claude extension. Apply only to an explicitly selected database.
CREATE TABLE cc_records (
 record_id text PRIMARY KEY, kind text NOT NULL, payload_hash text NOT NULL,
 payload jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cc_decisions (
 decision_id text PRIMARY KEY, work_item_id text NOT NULL UNIQUE, task_id text NOT NULL,
 prompt_sha256 text NOT NULL, payload jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE cc_reservations (
 call_id text PRIMARY KEY, work_item_id text NOT NULL, workflow_id text NOT NULL,
 profile text NOT NULL CHECK(profile IN ('haiku','sonnet','opus')),
 amount numeric NOT NULL CHECK(amount > 0), state text NOT NULL CHECK(state IN ('held','settled','released')),
 reserved_at timestamptz NOT NULL DEFAULT now(), terminal_at timestamptz
);
CREATE INDEX cc_reservations_workflow ON cc_reservations(workflow_id);
CREATE TABLE cc_receipts (
 receipt_id text PRIMARY KEY, call_id text NOT NULL UNIQUE REFERENCES cc_reservations(call_id),
 amount numeric NOT NULL CHECK(amount >= 0), receipt_sha256 text NOT NULL,
 rate_card_version text NOT NULL, counters jsonb NOT NULL, observed_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cc_receipts_observed ON cc_receipts(observed_at);
CREATE TABLE cc_subscription_observations (
 observation_id text PRIMARY KEY, provider text NOT NULL, window_id text NOT NULL,
 consumed_percent numeric NOT NULL CHECK(consumed_percent >= 0 AND consumed_percent <= 100),
 window_start timestamptz NOT NULL, resets_at timestamptz NOT NULL,
 observed_at timestamptz NOT NULL, valid_until timestamptz NOT NULL,
 producer_id text NOT NULL, evidence_sha256 text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 CHECK(window_start < resets_at AND observed_at < valid_until)
);
CREATE INDEX cc_subscription_latest ON cc_subscription_observations(provider,window_id,observed_at DESC);
