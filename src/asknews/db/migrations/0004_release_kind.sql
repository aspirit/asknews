-- Operational notices (auction schedules, facility terms, intervention data)
-- are neither statements nor reports. Give them their own kind.

ALTER TABLE documents DROP CONSTRAINT documents_kind_check;
ALTER TABLE documents ADD CONSTRAINT documents_kind_check CHECK (
    kind IN ('press_conference', 'statement', 'minutes', 'speech', 'report', 'release')
);
