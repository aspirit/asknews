-- Every model call made outside Ask (summaries, translations, briefs), with
-- its estimated cost. Together with ask_requests this is what the daily
-- spending cap is measured against.

CREATE TABLE llm_usage (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    called_at     timestamptz NOT NULL DEFAULT now(),
    purpose       text NOT NULL,            -- summary | brief
    model         text NOT NULL,
    input_tokens  integer NOT NULL,
    output_tokens integer NOT NULL,
    cost_usd      numeric(10, 6) NOT NULL,
    reference     text                      -- document id or meeting key
);

CREATE INDEX llm_usage_called_at_idx ON llm_usage (called_at DESC);
