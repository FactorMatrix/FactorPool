CREATE TABLE IF NOT EXISTS factors (
  id uuid PRIMARY KEY, name text NOT NULL, description text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS factor_versions (
  id uuid PRIMARY KEY, factor_id uuid NOT NULL REFERENCES factors(id), version integer NOT NULL,
  snapshot jsonb NOT NULL, source_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(factor_id,version)
);
CREATE TABLE IF NOT EXISTS research_batches (
  id uuid PRIMARY KEY, config jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_tasks (
  id uuid PRIMARY KEY, batch_id uuid REFERENCES research_batches(id), name text NOT NULL,
  snapshot jsonb NOT NULL, status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','CANCELLING','SUCCEEDED','FAILED','CANCELLED','INTERRUPTED')),
  error text, created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS research_results (
  task_id uuid PRIMARY KEY REFERENCES research_tasks(id), result jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS factor_values (
  task_id uuid NOT NULL REFERENCES research_tasks(id), ordinal bigint NOT NULL, trading_date date NOT NULL,
  symbol text NOT NULL, value double precision, valid boolean NOT NULL, ready boolean NOT NULL, reason text NOT NULL,
  forward_return double precision, factor_group integer, PRIMARY KEY(task_id,ordinal), UNIQUE(task_id,trading_date,symbol)
);
CREATE INDEX IF NOT EXISTS research_tasks_status_idx ON research_tasks(status,created_at);
CREATE INDEX IF NOT EXISTS factor_values_date_idx ON factor_values(task_id,trading_date,symbol);
