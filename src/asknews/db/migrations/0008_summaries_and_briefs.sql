-- Generated content. Everything in these columns and tables is produced by
-- a language model and is labelled as such wherever it is shown.

ALTER TABLE documents ADD COLUMN summary text;           -- in the document's language
ALTER TABLE documents ADD COLUMN summary_model text;

CREATE TABLE document_translations (
    document_id  bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    lang         text NOT NULL CHECK (lang IN ('en', 'ja')),
    title        text NOT NULL,
    summary      text,
    model        text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, lang)
);

-- Daily briefs cover a calendar day across publishers; meeting briefs cover
-- one policy meeting. Both are bilingual and cite the documents they draw on.
CREATE TABLE briefs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('daily', 'meeting')),
    brief_date   date NOT NULL,
    meeting_key  text,                      -- e.g. fed:20260916, set for meeting briefs
    title        jsonb NOT NULL,            -- {"en": ..., "ja": ...}
    points       jsonb NOT NULL,            -- [{"en": ..., "ja": ..., "documents": [id, ...]}]
    institutions text[] NOT NULL DEFAULT '{}',
    model        text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, brief_date, meeting_key)
);

CREATE INDEX briefs_date_idx ON briefs (brief_date DESC);
