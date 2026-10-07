-- Core archive: who published what, the text split into citable segments,
-- and one dense vector per segment.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE institutions (
    id      smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code    text NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_]{1,15}$'),
    name    text NOT NULL,
    website text NOT NULL
);

-- One row per published item. "kind" is the editorial type of the item,
-- not its file format; the original URL is always kept for attribution.
CREATE TABLE documents (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id smallint NOT NULL REFERENCES institutions (id),
    kind           text NOT NULL CHECK (
                       kind IN ('press_conference', 'statement', 'minutes', 'speech', 'report')
                   ),
    lang           text NOT NULL CHECK (lang IN ('en', 'ja')),
    external_id    text NOT NULL,
    title          text NOT NULL,
    published_at   timestamptz NOT NULL,
    source_url     text NOT NULL,
    body           text,
    meta           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (institution_id, kind, external_id)
);

CREATE INDEX documents_published_at_idx ON documents (published_at DESC);
CREATE INDEX documents_kind_lang_idx ON documents (kind, lang);

-- Video attached to a document, always hosted by the publisher's own player.
CREATE TABLE media (
    document_id  bigint PRIMARY KEY REFERENCES documents (id) ON DELETE CASCADE,
    provider     text NOT NULL CHECK (provider IN ('brightcove', 'youtube')),
    provider_id  text NOT NULL,
    duration_sec integer CHECK (duration_sec > 0),
    UNIQUE (provider, provider_id)
);

-- Retrieval unit. Timestamps are present when the source provides them
-- (captions); speaker is present for transcripts with attributed turns.
CREATE TABLE segments (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    position    integer NOT NULL CHECK (position >= 0),
    text        text NOT NULL CHECK (length(text) > 0),
    speaker     text,
    start_sec   real CHECK (start_sec >= 0),
    end_sec     real CHECK (end_sec >= start_sec),
    UNIQUE (document_id, position)
);

-- One embedding per segment. "model" records which encoder produced the
-- vector so a model change can be detected and re-embedded deliberately.
CREATE TABLE segment_embeddings (
    segment_id bigint PRIMARY KEY REFERENCES segments (id) ON DELETE CASCADE,
    model      text NOT NULL,
    embedding  vector(768) NOT NULL
);

CREATE INDEX segment_embeddings_hnsw_idx
    ON segment_embeddings USING hnsw (embedding vector_cosine_ops);
