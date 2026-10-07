CREATE EXTENSION IF NOT EXISTS vector;

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('none', 'trial', 'client');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE ticket_status AS ENUM ('new', 'in_progress', 'answered');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    role user_role NOT NULL DEFAULT 'none',
    trial_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    topic TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    version_date DATE NOT NULL DEFAULT CURRENT_DATE,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by BIGINT
);

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding vector(512) NOT NULL,
    topic TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    version_date DATE NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_topic_idx ON chunks (topic);
CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks (document_id);

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
    category TEXT NOT NULL,
    status ticket_status NOT NULL DEFAULT 'new',
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS files_library (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    title TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
