-- GTC Research Facts Schema
-- Reference copy of hub/supabase/migrations/20260618000000_cgt_research_facts.sql
-- Apply via hub: npx supabase db push

-- cgt_research_facts: universal schema v1 for research-level fact extraction
CREATE TABLE IF NOT EXISTS cgt_research_facts (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  abstract_id    TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  source_type    TEXT NOT NULL CHECK (source_type IN (
                   'pdf_abstract','pdf_report','web_article','nlm_report','pubmed')),
  fact_date      DATE,
  fact_type      TEXT NOT NULL CHECK (fact_type IN (
                   'finding','event','announcement','datapoint','claim')),
  subject        TEXT NOT NULL,
  what           TEXT NOT NULL,
  quant_value    FLOAT,
  quant_unit     TEXT,
  quant_context  TEXT,
  modality       TEXT CHECK (modality IN (
                   'gene_therapy','gene_editing','cell_therapy','mRNA','other')),
  disease        TEXT,
  organisation   TEXT,
  geography      TEXT,
  evidence_quote TEXT NOT NULL,
  citation       TEXT NOT NULL,
  confidence     TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
  schema_version TEXT NOT NULL DEFAULT 'v1',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (abstract_id, source_id, schema_version)
);

CREATE INDEX IF NOT EXISTS idx_cgt_research_facts_abstract_id  ON cgt_research_facts (abstract_id);
CREATE INDEX IF NOT EXISTS idx_cgt_research_facts_organisation  ON cgt_research_facts (organisation);
CREATE INDEX IF NOT EXISTS idx_cgt_research_facts_modality      ON cgt_research_facts (modality);
CREATE INDEX IF NOT EXISTS idx_cgt_research_facts_disease       ON cgt_research_facts (disease);

-- entities: normalised entity nodes for knowledge graph
CREATE TABLE IF NOT EXISTS entities (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_name TEXT UNIQUE NOT NULL,
  entity_type    TEXT NOT NULL CHECK (entity_type IN (
                   'company','person','disease','technology','conference')),
  aliases        TEXT[] NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- fact_entities: join table
CREATE TABLE IF NOT EXISTS fact_entities (
  fact_id    UUID NOT NULL REFERENCES cgt_research_facts(id) ON DELETE CASCADE,
  entity_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  role       TEXT,
  PRIMARY KEY (fact_id, entity_id)
);

-- cgt_facts_unified: view joining research + market intelligence
CREATE OR REPLACE VIEW cgt_facts_unified AS
  SELECT id::TEXT, abstract_id, source_id, source_type, fact_date, fact_type,
         subject, what, modality, disease, organisation, geography,
         evidence_quote, citation, confidence, schema_version, created_at
  FROM cgt_research_facts
  UNION ALL
  SELECT id::TEXT, dedup_key, source_name, 'web_article'::TEXT, source_date,
    CASE fact_category
      WHEN 'clinical' THEN 'finding' WHEN 'scientific' THEN 'finding'
      WHEN 'regulatory' THEN 'announcement' WHEN 'deal' THEN 'event'
      WHEN 'policy' THEN 'announcement' WHEN 'manufacturing' THEN 'announcement'
      ELSE 'finding' END,
    COALESCE(headline, LEFT(fact_text, 120)), fact_text,
    modality_tags[1], disease_tags[1], company_tags[1], geography_tags[1],
    evidence_quote, source_name || ', ' || source_date::TEXT,
    CASE confidence WHEN 'confirmed' THEN 'high' WHEN 'inferred' THEN 'medium'
      WHEN 'unverified' THEN 'low' ELSE 'medium' END,
    'intel_v1'::TEXT, created_at
  FROM cgt_intel_facts;
