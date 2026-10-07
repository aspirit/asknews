-- People who speak on the record: chairs, governors, board members and
-- anyone else whose speeches or press conferences the archive carries.
-- Display names in other languages live in domain configuration.

CREATE TABLE speakers (
    id             smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id smallint NOT NULL REFERENCES institutions (id),
    slug           text NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9-]{1,40}$'),
    name           text NOT NULL,
    role           text
);

-- The principal speaker of a speech or press conference. Statements, minutes
-- and reports are institutional and leave it NULL.
ALTER TABLE documents ADD COLUMN speaker_id smallint REFERENCES speakers (id);
CREATE INDEX documents_speaker_id_idx ON documents (speaker_id);

-- Hash of the segment texts last written for the document. Re-ingesting an
-- unchanged document can then skip the segment rewrite, which would otherwise
-- discard its embeddings.
ALTER TABLE documents ADD COLUMN content_hash text;
