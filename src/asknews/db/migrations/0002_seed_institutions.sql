-- The two publishers covered by the archive. Localized display names live in
-- the locale files; the database keeps one canonical English name.

INSERT INTO institutions (code, name, website) VALUES
    ('fed', 'Board of Governors of the Federal Reserve System', 'https://www.federalreserve.gov/'),
    ('boj', 'Bank of Japan', 'https://www.boj.or.jp/');
