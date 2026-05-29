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
    taxon TEXT DEFAULT 'implementation_pattern',
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    reasoning TEXT,
    scope TEXT DEFAULT 'project',
    applies_when TEXT DEFAULT '',
    does_not_apply_when TEXT DEFAULT '',
    evidence_level TEXT DEFAULT 'raw_observation',
    promotion_state TEXT DEFAULT 'candidate',
    project_path TEXT,
    source_lineage TEXT DEFAULT '{}',     -- JSON object
    evidence_excerpt TEXT DEFAULT '',
    counterexamples TEXT DEFAULT '[]',    -- JSON array
    conflicts_with TEXT DEFAULT '[]',     -- JSON array of knowledge ids
    review_required INTEGER DEFAULT 1,
    safe_to_generalize INTEGER DEFAULT 0,
    confidence REAL DEFAULT 0.5 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    source_session_id TEXT,
    related_ids TEXT DEFAULT '[]',        -- JSON array
    related_reasoning TEXT DEFAULT '{}',  -- JSON {id: why-related}
    tags TEXT DEFAULT '[]',               -- JSON array
    usage_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    approved INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    status TEXT DEFAULT 'standalone'      -- 'summary' | 'detail' | 'standalone'
        CHECK(status IN ('summary', 'detail', 'standalone')),
    parent_id TEXT,                       -- FK to knowledge(id) for detail→summary
    detail_ids TEXT DEFAULT '[]',         -- JSON array of detail ids (for summary)
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES knowledge(id)
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
    cache_read_input_tokens INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    cost_usd REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companion_interventions (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    project_path TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    knowledge_ids TEXT DEFAULT '[]',       -- JSON array
    intervention_type TEXT NOT NULL CHECK(intervention_type IN (
        'silent', 'hard_warning', 'soft_suggestion', 'clarifying_question'
    )),
    message TEXT NOT NULL,
    evidence_summary TEXT DEFAULT '',
    recommended_action TEXT DEFAULT '',
    confidence REAL DEFAULT 0.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    feedback_requested INTEGER DEFAULT 0,
    user_feedback TEXT,
    accepted INTEGER DEFAULT 0,
    dismissed INTEGER DEFAULT 0,
    noisy INTEGER DEFAULT 0,
    wrong INTEGER DEFAULT 0,
    context_snapshot TEXT DEFAULT '{}',    -- JSON object
    notification_channel TEXT DEFAULT 'terminal',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain);
CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge(type);
CREATE INDEX IF NOT EXISTS idx_knowledge_confidence ON knowledge(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_approved ON knowledge(approved);
CREATE INDEX IF NOT EXISTS idx_raw_sessions_extracted ON raw_sessions(extracted);
CREATE INDEX IF NOT EXISTS idx_companion_project ON companion_interventions(project_path);
CREATE INDEX IF NOT EXISTS idx_companion_type ON companion_interventions(intervention_type);
CREATE INDEX IF NOT EXISTS idx_companion_context_hash ON companion_interventions(context_hash);
