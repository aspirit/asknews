-- Runs once when the volume is first created: a separate database for the
-- test suite, so tests can reset it without touching development data.
CREATE DATABASE asknews_test;
