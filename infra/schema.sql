CREATE TABLE source_extracts (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_account TEXT,
    s3_uri TEXT NOT NULL,
    extract_started_at TIMESTAMPTZ NOT NULL,
    extract_completed_at TIMESTAMPTZ,
    checksum_sha256 TEXT,
    row_count BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE raw_ingest_batches (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL UNIQUE,
    source_system TEXT NOT NULL,
    dataset TEXT NOT NULL,
    source_account TEXT,
    schema_version TEXT,
    cursor TEXT,
    extracted_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    record_count BIGINT NOT NULL,
    storage_uri TEXT NOT NULL,
    manifest_uri TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL
);

CREATE TABLE companies (
    id BIGSERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    zoho_account_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE licenses (
    license_id TEXT PRIMARY KEY,
    company_id BIGINT REFERENCES companies(id),
    company_name_from_source TEXT,
    personnel_licensed INTEGER,
    eula_usage_threshold_gb_per_person NUMERIC(12, 2) NOT NULL DEFAULT 100.0,
    status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE organization_definitions (
    id BIGSERIAL PRIMARY KEY,
    license_id TEXT REFERENCES licenses(license_id),
    company_id BIGINT REFERENCES companies(id),
    allowed_countries TEXT[] NOT NULL DEFAULT '{}',
    allowed_states TEXT[] NOT NULL DEFAULT '{}',
    allowed_cities TEXT[] NOT NULL DEFAULT '{}',
    source_system TEXT NOT NULL,
    source_record_id TEXT,
    notes TEXT,
    effective_from TIMESTAMPTZ,
    effective_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE activations (
    id BIGSERIAL PRIMARY KEY,
    license_id TEXT NOT NULL REFERENCES licenses(license_id),
    company_name TEXT,
    activation_date TIMESTAMPTZ NOT NULL,
    license_entered_date TIMESTAMPTZ,
    status TEXT,
    ip_address INET,
    initial_product_version TEXT,
    deactivated_date TIMESTAMPTZ,
    source_extract_id BIGINT REFERENCES source_extracts(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE usage_records (
    id BIGSERIAL PRIMARY KEY,
    license_id TEXT NOT NULL REFERENCES licenses(license_id),
    company_name TEXT,
    links_processed BIGINT NOT NULL DEFAULT 0,
    files_processed BIGINT NOT NULL DEFAULT 0,
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    process_name TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    machine_name TEXT,
    username TEXT,
    mac_address TEXT,
    ip_address INET,
    tenant_name TEXT,
    site_name TEXT,
    tenant_id TEXT,
    database_name TEXT,
    source_extract_id BIGINT REFERENCES source_extracts(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ip_geolocations (
    ip_address INET PRIMARY KEY,
    city TEXT,
    state TEXT,
    country TEXT,
    latitude NUMERIC(9, 6),
    longitude NUMERIC(9, 6),
    accuracy_radius_km NUMERIC(12, 2),
    provider TEXT NOT NULL,
    confidence NUMERIC(5, 4),
    looked_up_at TIMESTAMPTZ NOT NULL,
    raw_response JSONB
);

CREATE TABLE investigation_reports (
    id BIGSERIAL PRIMARY KEY,
    subject TEXT NOT NULL,
    license_id TEXT,
    company_id BIGINT REFERENCES companies(id),
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    evaluation TEXT NOT NULL,
    total_links_processed BIGINT NOT NULL DEFAULT 0,
    total_files_processed BIGINT NOT NULL DEFAULT 0,
    total_file_size_bytes BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE investigation_findings (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES investigation_reports(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analyst_feedback (
    id BIGSERIAL PRIMARY KEY,
    report_id BIGINT REFERENCES investigation_reports(id) ON DELETE SET NULL,
    finding_id BIGINT REFERENCES investigation_findings(id) ON DELETE SET NULL,
    finding_code TEXT NOT NULL,
    accepted BOOLEAN NOT NULL,
    analyst_email TEXT NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_activations_license_date ON activations (license_id, activation_date);
CREATE INDEX idx_usage_license_start ON usage_records (license_id, start_time);
CREATE INDEX idx_usage_company ON usage_records (company_name);
CREATE INDEX idx_usage_tenant ON usage_records (tenant_id, tenant_name);
CREATE INDEX idx_findings_code ON investigation_findings (code);
CREATE INDEX idx_raw_ingest_source_dataset ON raw_ingest_batches (source_system, dataset, received_at);
