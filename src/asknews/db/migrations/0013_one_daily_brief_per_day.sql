-- A daily brief has no meeting key, and NULL keys never collided under the
-- unique constraint, so regenerating a day added a second brief instead of
-- replacing the first. Keep the newest brief per day and make NULL keys
-- compare equal.

DELETE FROM briefs older
 USING briefs newer
 WHERE older.kind = 'daily' AND newer.kind = 'daily'
   AND older.meeting_key IS NULL AND newer.meeting_key IS NULL
   AND older.brief_date = newer.brief_date
   AND (older.generated_at, older.id) < (newer.generated_at, newer.id);

ALTER TABLE briefs DROP CONSTRAINT briefs_kind_brief_date_meeting_key_key;
ALTER TABLE briefs ADD CONSTRAINT briefs_kind_brief_date_meeting_key_key
    UNIQUE NULLS NOT DISTINCT (kind, brief_date, meeting_key);
