-- Topic membership, computed once per document from the terms in
-- domain/topics.yaml (see "asknews topics"), so the rankings read a small
-- table instead of scanning document bodies on every request.

CREATE TABLE document_topics (
    document_id bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    topic       text NOT NULL,
    PRIMARY KEY (document_id, topic)
);

CREATE INDEX document_topics_topic_idx ON document_topics (topic);

-- The version of the term list a document was tagged with. Editing the
-- list changes the version and every document is tagged again.
ALTER TABLE documents ADD COLUMN topics_version text;
