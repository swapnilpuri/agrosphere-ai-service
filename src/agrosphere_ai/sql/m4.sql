-- AI-owned additive schema; only new generated fixtures may install this migration.
CREATE TABLE ai_source_revision (
 event_id uuid NOT NULL, body_hash char(64) NOT NULL,
 estate_id uuid NOT NULL, block_id uuid NOT NULL,
 first_received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 body jsonb NOT NULL, PRIMARY KEY(event_id,body_hash)
);
CREATE INDEX ai_source_scope ON ai_source_revision(estate_id,block_id);
CREATE TABLE ai_conflict (
 estate_id uuid NOT NULL, block_id uuid NOT NULL, event_id uuid NOT NULL,
 source_at timestamptz NOT NULL, available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(estate_id,block_id,event_id,source_at,available_at)
);
CREATE TABLE ai_dataset (
 dataset_id char(64) PRIMARY KEY, corpus_hash char(64) NOT NULL,
 estate_id uuid NOT NULL, block_id uuid NOT NULL, body jsonb NOT NULL,
 UNIQUE(corpus_hash,estate_id,block_id)
);
CREATE TABLE ai_model (
 model_id char(64) NOT NULL, dataset_id char(64) NOT NULL REFERENCES ai_dataset,
 estate_id uuid NOT NULL, block_id uuid NOT NULL, body jsonb NOT NULL,
 PRIMARY KEY(model_id,dataset_id)
);
CREATE TABLE ai_evaluation (
 evaluation_id char(64) PRIMARY KEY, dataset_id char(64) NOT NULL REFERENCES ai_dataset,
 estate_id uuid NOT NULL, block_id uuid NOT NULL, body jsonb NOT NULL
);
CREATE TABLE ai_evaluation_source (
 evaluation_id char(64) NOT NULL REFERENCES ai_evaluation,
 event_id uuid NOT NULL, body_hash char(64) NOT NULL,
 PRIMARY KEY(evaluation_id,event_id,body_hash)
);
CREATE FUNCTION ai_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'M4 records are append-only'; END;
$$;
CREATE TRIGGER ai_source_immutable BEFORE UPDATE OR DELETE ON ai_source_revision FOR EACH ROW EXECUTE FUNCTION ai_immutable();
CREATE TRIGGER ai_conflict_immutable BEFORE UPDATE OR DELETE ON ai_conflict FOR EACH ROW EXECUTE FUNCTION ai_immutable();
CREATE TRIGGER ai_dataset_immutable BEFORE UPDATE OR DELETE ON ai_dataset FOR EACH ROW EXECUTE FUNCTION ai_immutable();
CREATE TRIGGER ai_model_immutable BEFORE UPDATE OR DELETE ON ai_model FOR EACH ROW EXECUTE FUNCTION ai_immutable();
CREATE TRIGGER ai_evaluation_immutable BEFORE UPDATE OR DELETE ON ai_evaluation FOR EACH ROW EXECUTE FUNCTION ai_immutable();
CREATE TRIGGER ai_evaluation_source_immutable BEFORE UPDATE OR DELETE ON ai_evaluation_source FOR EACH ROW EXECUTE FUNCTION ai_immutable();
