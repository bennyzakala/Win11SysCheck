ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN banned BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN terms_at BIGINT;
ALTER TABLE reports ADD COLUMN status TEXT NOT NULL DEFAULT 'open';
ALTER TABLE reports ADD COLUMN resolved_at BIGINT;
ALTER TABLE reports ADD COLUMN resolved_by INTEGER;
CREATE INDEX reports_status ON reports (status, created_at);
-- One-time links sent by email (address verification and password reset); only a hash of the token is stored
CREATE TABLE tokens (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  expires_at BIGINT NOT NULL
);
CREATE INDEX tokens_user ON tokens (user_id, kind);
