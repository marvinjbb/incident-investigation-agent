CREATE TABLE IF NOT EXISTS demo_lock_target (
    id integer PRIMARY KEY,
    value integer NOT NULL
);

INSERT INTO demo_lock_target (id, value)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS demo_workload (
    id integer PRIMARY KEY,
    message text NOT NULL
);

INSERT INTO demo_workload (id, message)
VALUES (1, 'workload completed')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS incidents (
    incident_id uuid PRIMARY KEY,
    scenario text NOT NULL CHECK (scenario IN ('blocked_query', 'connection_exhaustion', 'bad_deployment')),
    status text NOT NULL CHECK (status IN ('starting', 'active', 'recovering', 'resolved', 'failed')),
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    description text NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_events (
    event_id bigserial PRIMARY KEY,
    incident_id uuid NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS incident_events_incident_time_idx
ON incident_events (incident_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS deployments (
    deployment_id uuid PRIMARY KEY,
    version text NOT NULL,
    deployed_at timestamptz NOT NULL,
    status text NOT NULL,
    became_active boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS application_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_version text NOT NULL
);

INSERT INTO application_state (singleton, active_version)
VALUES (true, 'v1')
ON CONFLICT (singleton) DO NOTHING;

INSERT INTO deployments (deployment_id, version, deployed_at, status, became_active)
SELECT '00000000-0000-0000-0000-000000000001', 'v1', now(), 'healthy', true
WHERE NOT EXISTS (SELECT 1 FROM deployments WHERE version = 'v1');

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id uuid PRIMARY KEY,
    incident_id uuid NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    model text NOT NULL,
    report jsonb,
    tool_trace jsonb NOT NULL DEFAULT '[]'::jsonb,
    error_type text
);

CREATE INDEX IF NOT EXISTS investigations_incident_started_idx
ON investigations (incident_id, started_at DESC);

CREATE TABLE IF NOT EXISTS remediation_proposals (
    proposal_id uuid PRIMARY KEY,
    incident_id uuid NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    investigation_id uuid NOT NULL REFERENCES investigations(investigation_id) ON DELETE CASCADE,
    action_type text NOT NULL CHECK (action_type IN (
        'terminate_demo_blocker',
        'release_demo_pool_pressure',
        'rollback_demo_deployment'
    )),
    status text NOT NULL CHECK (status IN (
        'pending_approval', 'approved', 'executing', 'succeeded',
        'failed', 'rejected', 'expired'
    )),
    summary text NOT NULL,
    reason text NOT NULL,
    supporting_evidence_ids jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    approved_at timestamptz,
    executed_at timestamptz,
    completed_at timestamptz,
    execution_result jsonb,
    verification_result jsonb
);

CREATE INDEX IF NOT EXISTS remediation_incident_created_idx
ON remediation_proposals (incident_id, created_at DESC);

CREATE TABLE IF NOT EXISTS remediation_audit_events (
    audit_event_id bigserial PRIMARY KEY,
    proposal_id uuid NOT NULL REFERENCES remediation_proposals(proposal_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS remediation_audit_proposal_time_idx
ON remediation_audit_events (proposal_id, occurred_at, audit_event_id);
