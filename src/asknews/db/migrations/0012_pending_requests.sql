-- A request is recorded before the model is called, with the cost an answer
-- may reach, so that concurrent questions cannot slip past the daily cap or
-- a client's rate together. The row is completed when the answer is in.

ALTER TABLE ask_requests DROP CONSTRAINT ask_requests_status_check;
ALTER TABLE ask_requests ADD CONSTRAINT ask_requests_status_check CHECK (
    status IN ('pending', 'answered', 'declined', 'no_sources', 'error')
);
