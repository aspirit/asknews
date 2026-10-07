-- Japanese names for speakers, from domain/speakers.yaml, so the Japanese
-- interface shows a person's name in Japanese rather than romanised.

ALTER TABLE speakers ADD COLUMN name_ja text;
