CREATE TABLE IF NOT EXISTS profiles (
    tenant_id text NOT NULL,
    subject_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    source_version bigint NOT NULL CHECK (source_version > 0),
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESTRICTED')),
    PRIMARY KEY (tenant_id, subject_id, generation)
);

CREATE TABLE IF NOT EXISTS documents (
    document_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    subject_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    source_version bigint NOT NULL CHECK (source_version > 0),
    object_key text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESTRICTED'))
);
CREATE INDEX IF NOT EXISTS documents_subject_idx
    ON documents (tenant_id, subject_id, generation, status);

CREATE TABLE IF NOT EXISTS index_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL,
    tenant_id text NOT NULL,
    subject_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    source_version bigint NOT NULL CHECK (source_version > 0),
    chunk_text text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RESTRICTED'))
);
CREATE INDEX IF NOT EXISTS index_subject_idx
    ON index_chunks (tenant_id, subject_id, generation, status);

CREATE TABLE IF NOT EXISTS operation_journal (
    operation_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    subject_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    authority_epoch bigint NOT NULL CHECK (authority_epoch >= 0),
    source_version bigint NOT NULL CHECK (source_version > 0),
    outcome text NOT NULL CHECK (outcome IN ('APPLIED', 'STALE_VERSION', 'FAILED')),
    committed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS journal_subject_idx
    ON operation_journal (tenant_id, subject_id, generation);

CREATE TABLE IF NOT EXISTS lineage_edges (
    tenant_id text NOT NULL,
    subject_id text NOT NULL,
    generation bigint NOT NULL CHECK (generation > 0),
    source_type text NOT NULL,
    source_id text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, generation, source_type, source_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS lineage_subject_idx
    ON lineage_edges (tenant_id, subject_id, generation);
