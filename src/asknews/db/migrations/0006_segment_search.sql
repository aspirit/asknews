-- Lexical search over segments. Each segment carries its document's language
-- so a generated tsvector can pick the right configuration; a trigram index
-- serves substring matching for Japanese and exact phrases in any language.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE segments ADD COLUMN lang text;
UPDATE segments s SET lang = d.lang FROM documents d WHERE d.id = s.document_id;
ALTER TABLE segments ALTER COLUMN lang SET NOT NULL;

CREATE FUNCTION segments_set_lang() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.lang IS NULL THEN
        SELECT lang INTO NEW.lang FROM documents WHERE id = NEW.document_id;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER segments_lang BEFORE INSERT ON segments
    FOR EACH ROW EXECUTE FUNCTION segments_set_lang();

ALTER TABLE segments ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    to_tsvector(
        CASE WHEN lang = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END,
        text
    )
) STORED;

CREATE INDEX segments_tsv_idx ON segments USING gin (tsv);
CREATE INDEX segments_text_trgm_idx ON segments USING gin (text gin_trgm_ops);
CREATE INDEX segments_document_id_idx ON segments (document_id);
