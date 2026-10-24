CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  pass TEXT NOT NULL,
  name TEXT NOT NULL,
  birthdate TEXT NOT NULL,
  city TEXT NOT NULL,
  gender TEXT, job TEXT, height INTEGER, edu TEXT, hometown TEXT, langs TEXT, zodiac TEXT, status TEXT,
  relig TEXT, intent TEXT, kids TEXT, want_kids TEXT, pets TEXT, drink TEXT, smoke TEXT, sport TEXT, kosher TEXT,
  prompts JSONB NOT NULL DEFAULT '[]',
  created_at BIGINT NOT NULL
);
CREATE TABLE sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at BIGINT NOT NULL
);
CREATE TABLE photos (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  file TEXT NOT NULL UNIQUE,
  position INTEGER NOT NULL
);
CREATE INDEX photos_user ON photos (user_id);
CREATE TABLE likes (
  from_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  to_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  idx INTEGER NOT NULL,
  note TEXT,
  rose BOOLEAN NOT NULL DEFAULT false,
  removed BOOLEAN NOT NULL DEFAULT false,
  created_at BIGINT NOT NULL,
  PRIMARY KEY (from_id, to_id)
);
CREATE INDEX likes_to ON likes (to_id);
CREATE TABLE skips (
  from_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  to_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (from_id, to_id)
);
CREATE TABLE matches (
  id SERIAL PRIMARY KEY,
  a INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  b INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ended BOOLEAN NOT NULL DEFAULT false,
  created_at BIGINT NOT NULL,
  UNIQUE (a, b)
);
CREATE TABLE messages (
  id SERIAL PRIMARY KEY,
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  from_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at BIGINT NOT NULL
);
CREATE INDEX messages_match ON messages (match_id, id);
CREATE TABLE reports (
  id SERIAL PRIMARY KEY,
  from_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  to_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  reason TEXT,
  created_at BIGINT NOT NULL
);
CREATE TABLE attempts (key TEXT NOT NULL, at BIGINT NOT NULL);
CREATE INDEX attempts_key ON attempts (key, at);
