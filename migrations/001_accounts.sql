-- HpAgent cross-client memory: accounts, sessions, events
-- Run against the app PostgreSQL instance.

CREATE TABLE IF NOT EXISTS accounts (
    account_id  TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_bindings (
    account_id       TEXT REFERENCES accounts(account_id) ON DELETE CASCADE,
    channel_type     TEXT NOT NULL,
    channel_user_id  TEXT NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (channel_type, channel_user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    account_id   TEXT REFERENCES accounts(account_id) ON DELETE CASCADE,
    status       TEXT DEFAULT 'active',
    channel_type TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT REFERENCES sessions(session_id) ON DELETE CASCADE,
    event_index  INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    content      JSONB DEFAULT '{}',
    metadata     JSONB DEFAULT '{}',
    timestamp    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, event_index);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id, status);
CREATE INDEX IF NOT EXISTS idx_bindings_lookup ON account_bindings(channel_type, channel_user_id);
