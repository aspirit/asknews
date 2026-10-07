-- Every question asked, with what was retrieved, what was answered and what
-- it cost. This is the only usage data the site keeps: no accounts, and the
-- client is recorded as a salted daily hash used for rate limiting alone.

CREATE TABLE ask_requests (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asked_at      timestamptz NOT NULL DEFAULT now(),
    lang          text NOT NULL CHECK (lang IN ('en', 'ja')),
    question      text NOT NULL,
    status        text NOT NULL CHECK (status IN ('answered', 'declined', 'no_sources', 'error')),
    answer        jsonb,
    citations     jsonb NOT NULL DEFAULT '[]'::jsonb,
    model         text,
    input_tokens  integer,
    output_tokens integer,
    cost_usd      numeric(10, 6),
    latency_ms    integer,
    client_hash   text,
    meta          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX ask_requests_asked_at_idx ON ask_requests (asked_at DESC);
CREATE INDEX ask_requests_client_idx ON ask_requests (client_hash, asked_at DESC);
