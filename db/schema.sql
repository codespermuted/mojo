-- Mojo: Knowledge Mojoation for Claude Code
-- Schema v1.0

CREATE TABLE IF NOT EXISTS raw_sessions (
    id TEXT PRIMARY KEY,
    transcript_path TEXT NOT NULL,
    project_hash TEXT,
    project_path TEXT,
    started_at TEXT,
    ended_at TEXT,
    turn_count INTEGER,
    has_corrections INTEGER DEFAULT 0,
    extracted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN (
        'domain_rule', 'architecture_decision', 'debug_playbook',
        'anti_pattern', 'tool_preference', 'code_pattern'
    )),
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    reasoning TEXT,
    confidence REAL DEFAULT 0.5 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    source_session_id TEXT,
    related_ids TEXT DEFAULT '[]',       -- JSON array
    tags TEXT DEFAULT '[]',              -- JSON array
    usage_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    approved INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS injections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    target TEXT NOT NULL CHECK(target IN ('claude_md', 'skill')),
    accepted INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id)
);

CREATE TABLE IF NOT EXISTS extraction_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('filter', 'structure')),
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain);
CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge(type);
CREATE INDEX IF NOT EXISTS idx_knowledge_confidence ON knowledge(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_approved ON knowledge(approved);
CREATE INDEX IF NOT EXISTS idx_raw_sessions_extracted ON raw_sessions(extracted);
