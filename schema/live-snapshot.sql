--
-- PostgreSQL database dump
--

\restrict ruf1Kbwt2NhutCnr3gj1mSGIkR6G3aJSHlU89UboWihxaZs0iCQOOZGhQc3Q1ki

-- Dumped from database version 18.4
-- Dumped by pg_dump version 18.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: enforce_merge_target(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_merge_target() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE
    target_is_merged BOOLEAN;
    is_a_survivor    BOOLEAN;
BEGIN
    IF NEW.merged_into_id IS NULL THEN
        RETURN NEW;
    END IF;
    -- One hop, always: if A merges into B and B into C, a reader following one hop lands on
    -- a tombstone, and every consumer would need a recursive CTE.
    EXECUTE format(
        'SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 AND merged_into_id IS NOT NULL)',
        TG_TABLE_NAME) INTO target_is_merged USING NEW.merged_into_id;
    IF target_is_merged THEN
        RAISE EXCEPTION '% % is itself merged; point the merge at its survivor instead',
            TG_TABLE_NAME, NEW.merged_into_id;
    END IF;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE merged_into_id = $1)',
        TG_TABLE_NAME) INTO is_a_survivor USING NEW.id;
    IF is_a_survivor THEN
        RAISE EXCEPTION '% % is the survivor of another merge and cannot itself be merged',
            TG_TABLE_NAME, NEW.id;
    END IF;
    RETURN NEW;
END;
$_$;


--
-- Name: enforce_vocabulary(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_vocabulary() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_name   TEXT := TG_ARGV[0];
    col_name TEXT := TG_ARGV[1];
    pk_col   TEXT := COALESCE(TG_ARGV[2], 'id');
    rec      JSONB := to_jsonb(NEW);
    val      TEXT  := rec ->> col_name;
    pk_val   BIGINT;
    fb       TEXT;
BEGIN
    IF val IS NULL OR val = '' THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1 FROM vocabulary_terms
        WHERE vocabulary = v_name AND term = val AND deprecated_by_term IS NULL
    ) THEN
        RETURN NEW;
    END IF;

    -- Unknown term. Log it, substitute the fallback if one is defined, continue.
    SELECT fallback_term INTO fb FROM vocabularies WHERE name = v_name;
    pk_val := (rec ->> pk_col)::BIGINT;   -- serial defaults are assigned before BEFORE triggers

    INSERT INTO vocabulary_proposals
        (vocabulary, proposed_term, written_as, entity_table, entity_id)
    VALUES (v_name, val, COALESCE(fb, val), TG_TABLE_NAME, pk_val)
    ON CONFLICT (vocabulary, proposed_term, entity_table) DO UPDATE
        SET occurrences  = vocabulary_proposals.occurrences + 1,
            last_seen_at = now();

    IF fb IS NOT NULL THEN
        rec := jsonb_set(rec, ARRAY[col_name], to_jsonb(fb));
        NEW := jsonb_populate_record(NEW, rec);
    END IF;

    RETURN NEW;   -- NEVER reject
END;
$$;


--
-- Name: reject_private_voiceprint(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_private_voiceprint() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT (SELECT is_public_figure FROM persons WHERE id = NEW.person_id) THEN
        RAISE EXCEPTION
          'Refusing voiceprint for person % — is_public_figure is FALSE. '
          'Private individuals get name-based identification only.', NEW.person_id;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: require_curated_claim(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.require_curated_claim() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (SELECT subject_id IS NULL OR curation_state <> 'confirmed'
        FROM claims WHERE id = NEW.claim_id) THEN
        RAISE EXCEPTION
          'Claim % cannot enter a page manifest: subject_id unset or curation_state '
          'is not confirmed.', NEW.claim_id;
    END IF;
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: affiliations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.affiliations (
    id integer NOT NULL,
    person_id integer NOT NULL,
    org_id integer NOT NULL,
    role text,
    valid_from date,
    valid_to date,
    source text NOT NULL,
    confidence numeric(3,2),
    reviewed_by text,
    CONSTRAINT affiliations_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))
);


--
-- Name: affiliations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.affiliations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: affiliations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.affiliations_id_seq OWNED BY public.affiliations.id;


--
-- Name: arguments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.arguments (
    id integer NOT NULL,
    canonical_name text NOT NULL,
    description text,
    argument_class text,
    named_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: arguments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.arguments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: arguments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.arguments_id_seq OWNED BY public.arguments.id;


--
-- Name: asserted_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asserted_events (
    id bigint NOT NULL,
    subject_id integer,
    canonical_description text NOT NULL,
    event_class text,
    occurred_start date,
    occurred_end date,
    occurred_precision text,
    occurred_calendar text DEFAULT 'calendar'::text NOT NULL,
    legistar_event_item_id integer,
    named_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT asserted_events_check CHECK (((occurred_end IS NULL) OR (occurred_start IS NULL) OR (occurred_end >= occurred_start)))
);


--
-- Name: asserted_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asserted_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asserted_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asserted_events_id_seq OWNED BY public.asserted_events.id;


--
-- Name: barriers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.barriers (
    id bigint NOT NULL,
    subject_id integer,
    issue_id integer,
    claim_id bigint NOT NULL,
    barrier_class text NOT NULL,
    barrier_text text NOT NULL,
    named_by_person_id integer,
    resolution_status text DEFAULT 'unresolved'::text,
    verbatim text NOT NULL,
    CONSTRAINT barriers_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: barriers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.barriers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: barriers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.barriers_id_seq OWNED BY public.barriers.id;


--
-- Name: bodies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bodies (
    id integer NOT NULL,
    jurisdiction_id integer NOT NULL,
    legistar_body_id integer,
    name text NOT NULL,
    classification text,
    authority_type text,
    active boolean DEFAULT true,
    created_date date,
    dissolved_date date,
    description text
);


--
-- Name: bodies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bodies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bodies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bodies_id_seq OWNED BY public.bodies.id;


--
-- Name: body_lineage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.body_lineage (
    id integer NOT NULL,
    successor_body_id integer NOT NULL,
    predecessor_body_id integer NOT NULL,
    relation text NOT NULL,
    effective_date date NOT NULL,
    authorizing_matter_id integer,
    source text NOT NULL
);


--
-- Name: body_lineage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.body_lineage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: body_lineage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.body_lineage_id_seq OWNED BY public.body_lineage.id;


--
-- Name: claim_conditions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_conditions (
    id bigint NOT NULL,
    claim_id bigint NOT NULL,
    condition_text text NOT NULL,
    condition_types text[],
    condition_origin text,
    is_met text DEFAULT 'unknown'::text,
    resolved_by_claim_id bigint,
    resolved_at_ms integer,
    target_date date,
    verbatim text NOT NULL,
    CONSTRAINT claim_conditions_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: claim_conditions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.claim_conditions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claim_conditions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.claim_conditions_id_seq OWNED BY public.claim_conditions.id;


--
-- Name: claim_prior_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_prior_references (
    id bigint NOT NULL,
    claim_id bigint NOT NULL,
    referenced_event_id integer,
    referenced_body_id integer,
    referenced_period text,
    reference_text text NOT NULL
);


--
-- Name: claim_prior_references_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.claim_prior_references_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claim_prior_references_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.claim_prior_references_id_seq OWNED BY public.claim_prior_references.id;


--
-- Name: claim_reasons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_reasons (
    id bigint NOT NULL,
    claim_id bigint NOT NULL,
    argument_id integer,
    reason_text text NOT NULL,
    reason_class text,
    rationale_frame text,
    sequence integer,
    verbatim text NOT NULL,
    embedding public.vector(1536),
    embedding_model text,
    embedding_version text,
    CONSTRAINT claim_reasons_check CHECK (((embedding IS NULL) OR (embedding_model IS NOT NULL))),
    CONSTRAINT claim_reasons_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: claim_reasons_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.claim_reasons_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claim_reasons_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.claim_reasons_id_seq OWNED BY public.claim_reasons.id;


--
-- Name: claim_relations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_relations (
    id bigint NOT NULL,
    claim_a_id bigint NOT NULL,
    claim_b_id bigint NOT NULL,
    relation text NOT NULL,
    confidence numeric(3,2),
    evidence text,
    CONSTRAINT claim_relations_check CHECK ((claim_a_id <> claim_b_id))
);


--
-- Name: claim_relations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.claim_relations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claim_relations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.claim_relations_id_seq OWNED BY public.claim_relations.id;


--
-- Name: claim_subject_mentions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claim_subject_mentions (
    id bigint NOT NULL,
    claim_id bigint NOT NULL,
    subject_id integer NOT NULL,
    span_start integer NOT NULL,
    span_end integer NOT NULL,
    matched_text text NOT NULL,
    method text NOT NULL,
    detected_by text NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_by text,
    confirmed_at timestamp with time zone,
    CONSTRAINT claim_subject_mentions_check CHECK ((span_end > span_start)),
    CONSTRAINT claim_subject_mentions_check1 CHECK (((confirmed_by IS NULL) OR (confirmed_at IS NOT NULL)))
);


--
-- Name: TABLE claim_subject_mentions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.claim_subject_mentions IS 'A claim NAMES a subject. Weaker than claims.subject_id, which is what the claim is about, and many-to-many because one sentence can name several. Every row is provable by slicing claims.verbatim at span_start..span_end.';


--
-- Name: claim_subject_mentions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.claim_subject_mentions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claim_subject_mentions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.claim_subject_mentions_id_seq OWNED BY public.claim_subject_mentions.id;


--
-- Name: claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.claims (
    id bigint NOT NULL,
    subject_id integer,
    curation_state text DEFAULT 'unassigned'::text NOT NULL,
    issue_id integer,
    matter_id integer,
    actor_id integer,
    actor_capacity text,
    org_id integer,
    "position" text NOT NULL,
    polarity text NOT NULL,
    contested_dimension text,
    modality text,
    speech_act text,
    competing_subject_id integer,
    asserted_start date,
    asserted_end date,
    asserted_precision text,
    asserted_calendar text DEFAULT 'calendar'::text NOT NULL,
    asserted_date_text text,
    date_confidence numeric(3,2),
    date_validation_flag boolean DEFAULT false,
    outcome_without_mechanism boolean DEFAULT false NOT NULL,
    source_type text NOT NULL,
    utterance_id bigint,
    document_id integer,
    reported_by_document_id integer,
    source_content_hash text,
    span_start integer,
    span_end integer,
    span_unit text,
    verbatim text NOT NULL,
    extra_spans jsonb,
    confidence numeric(3,2),
    evidence_grade character(1) DEFAULT 'C'::bpchar NOT NULL,
    extracted_by text NOT NULL,
    reviewed_by text,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    document_section_id integer,
    CONSTRAINT claims_check CHECK (((utterance_id IS NOT NULL) OR (document_id IS NOT NULL))),
    CONSTRAINT claims_check1 CHECK (((asserted_end IS NULL) OR (asserted_start IS NULL) OR (asserted_end >= asserted_start))),
    CONSTRAINT claims_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
    CONSTRAINT claims_evidence_grade_check CHECK ((evidence_grade = ANY (ARRAY['A'::bpchar, 'B'::bpchar, 'C'::bpchar, 'D'::bpchar]))),
    CONSTRAINT claims_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: claims_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.claims_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: claims_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.claims_id_seq OWNED BY public.claims.id;


--
-- Name: coalition_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coalition_members (
    coalition_id integer NOT NULL,
    person_id integer,
    org_id integer,
    body_id integer,
    role text,
    claim_id bigint,
    id bigint NOT NULL,
    CONSTRAINT coalition_members_one_member CHECK ((num_nonnulls(person_id, org_id, body_id) = 1))
);


--
-- Name: COLUMN coalition_members.role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.coalition_members.role IS 'How this actor is involved -- lead, community_partner, funder. Controlled, because an uncontrolled role field cannot answer "who leads this" across a corpus.';


--
-- Name: coalition_members_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.coalition_members_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: coalition_members_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.coalition_members_id_seq OWNED BY public.coalition_members.id;


--
-- Name: coalitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coalitions (
    id integer NOT NULL,
    subject_id integer,
    name text,
    coalition_type text,
    formed_date date,
    purpose text
);


--
-- Name: coalitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.coalitions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: coalitions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.coalitions_id_seq OWNED BY public.coalitions.id;


--
-- Name: commitments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commitments (
    id bigint NOT NULL,
    subject_id integer,
    committed_by_person_id integer,
    committed_by_org_id integer,
    commitment_text text NOT NULL,
    due_date date,
    due_description text,
    status text DEFAULT 'open'::text NOT NULL,
    fulfilled_by_document_id integer,
    fulfilled_by_event_id integer,
    source_type text NOT NULL,
    utterance_id bigint,
    document_id integer,
    verbatim text NOT NULL,
    CONSTRAINT commitments_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: commitments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.commitments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: commitments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.commitments_id_seq OWNED BY public.commitments.id;


--
-- Name: considered_alternatives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.considered_alternatives (
    id bigint NOT NULL,
    subject_id integer NOT NULL,
    alternative_name text NOT NULL,
    disposition text NOT NULL,
    rejection_class text,
    rejection_reason text,
    superseded_by_alternative_id bigint,
    claim_id bigint,
    is_scope_condition_candidate boolean DEFAULT true NOT NULL,
    verbatim text NOT NULL,
    CONSTRAINT considered_alternatives_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: considered_alternatives_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.considered_alternatives_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: considered_alternatives_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.considered_alternatives_id_seq OWNED BY public.considered_alternatives.id;


--
-- Name: curation_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curation_decisions (
    id bigint NOT NULL,
    entity_table text NOT NULL,
    entity_a_id bigint NOT NULL,
    entity_b_id bigint,
    decision text NOT NULL,
    decided_by text NOT NULL,
    decided_at timestamp with time zone DEFAULT now(),
    rationale text
);


--
-- Name: curation_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.curation_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: curation_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.curation_decisions_id_seq OWNED BY public.curation_decisions.id;


--
-- Name: decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decisions (
    id bigint NOT NULL,
    event_item_id integer,
    subject_id integer,
    decision_type text NOT NULL,
    motion_text text,
    mover_person_id integer,
    seconder_person_id integer,
    threshold_required text,
    outcome text,
    effective_date date,
    sunset_date date,
    utterance_id bigint,
    verbatim text
);


--
-- Name: decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.decisions_id_seq OWNED BY public.decisions.id;


--
-- Name: document_figures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_figures (
    id integer NOT NULL,
    document_id integer NOT NULL,
    page_no integer NOT NULL,
    bbox jsonb NOT NULL,
    classifier_label text NOT NULL,
    classifier_conf numeric(4,3),
    caption text,
    crop_path text,
    crop_dpi integer,
    extracted_by text NOT NULL,
    prompt_version text NOT NULL,
    raw_xml text NOT NULL,
    extracted_at timestamp with time zone NOT NULL,
    source_content_hash text,
    CONSTRAINT document_figures_raw_xml_check CHECK ((length(TRIM(BOTH FROM raw_xml)) > 0))
);


--
-- Name: document_figures_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.document_figures_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_figures_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.document_figures_id_seq OWNED BY public.document_figures.id;


--
-- Name: document_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_links (
    id bigint NOT NULL,
    document_id integer NOT NULL,
    uri text NOT NULL,
    anchor_text text NOT NULL,
    context_sentence text,
    page_no integer,
    char_start integer,
    char_end integer,
    located boolean DEFAULT false NOT NULL,
    source_content_hash text,
    harvested_at timestamp with time zone NOT NULL,
    fetched_at timestamp with time zone,
    CONSTRAINT document_links_anchor_text_check CHECK ((length(TRIM(BOTH FROM anchor_text)) > 0)),
    CONSTRAINT document_links_uri_check CHECK ((length(TRIM(BOTH FROM uri)) > 0))
);


--
-- Name: document_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.document_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.document_links_id_seq OWNED BY public.document_links.id;


--
-- Name: document_sections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_sections (
    id integer NOT NULL,
    document_id integer NOT NULL,
    sequence integer NOT NULL,
    heading text,
    section_topic text,
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    page_start integer,
    page_end integer,
    extraction_tier character(1) DEFAULT 'C'::bpchar NOT NULL,
    tier_assigned_by text,
    summary text,
    content_hash text NOT NULL,
    parse_confidence text DEFAULT 'unaudited'::text NOT NULL,
    parse_flags jsonb,
    parse_reviewed_by text,
    parse_reviewed_at timestamp with time zone,
    human_verdict text,
    human_verdict_by text,
    human_verdict_at timestamp with time zone,
    human_verdict_note text,
    human_verdict_hash text,
    subject_id integer,
    CONSTRAINT document_sections_check CHECK ((char_end > char_start)),
    CONSTRAINT document_sections_extraction_tier_check CHECK ((extraction_tier = ANY (ARRAY['A'::bpchar, 'B'::bpchar, 'C'::bpchar]))),
    CONSTRAINT sections_human_verdict_attributed CHECK (((human_verdict IS NULL) OR (human_verdict = 'not_reviewed'::text) OR ((human_verdict_by IS NOT NULL) AND (human_verdict_hash IS NOT NULL))))
);


--
-- Name: COLUMN document_sections.human_verdict; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.document_sections.human_verdict IS 'A person''s judgement, independent of parse_confidence. Only valid while human_verdict_hash equals content_hash -- an approval of text that has since changed is not an approval of the text in the store.';


--
-- Name: COLUMN document_sections.subject_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.document_sections.subject_id IS 'What this section is about, from the report''s own structure. Claims inherit it. NULL on navigational sections -- a table of contents is not about anything, and giving it a subject would put structural furniture into topic aggregates.';


--
-- Name: document_sections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.document_sections_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_sections_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.document_sections_id_seq OWNED BY public.document_sections.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id integer NOT NULL,
    jurisdiction_id integer NOT NULL,
    event_id integer,
    matter_id integer,
    doc_type text NOT NULL,
    title text,
    source_url text,
    published_date date,
    covers_period_start date,
    covers_period_end date,
    retrieved_at timestamp with time zone,
    is_mutable_source boolean DEFAULT false NOT NULL,
    snapshot_path text,
    snapshot_hash text,
    markdown_path text,
    page_count integer,
    content_hash text,
    converter text,
    converter_version text,
    parse_verdict text,
    parse_override_reason text,
    covers_period_source text,
    covers_period_note text,
    snapshot_source text,
    snapshot_note text,
    CONSTRAINT documents_check CHECK (((NOT is_mutable_source) OR (snapshot_path IS NOT NULL))),
    CONSTRAINT documents_estimate_needs_a_note CHECK (((covers_period_source IS DISTINCT FROM 'human_estimate'::text) OR (covers_period_note IS NOT NULL))),
    CONSTRAINT documents_refusal_needs_a_reason CHECK (((parse_verdict IS DISTINCT FROM 'REFUSE'::text) OR (parse_override_reason IS NOT NULL))),
    CONSTRAINT documents_snapshot_pair CHECK (((snapshot_path IS NULL) = (snapshot_hash IS NULL)))
);


--
-- Name: COLUMN documents.snapshot_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.documents.snapshot_source IS 'How snapshot_path/snapshot_hash/retrieved_at were established. local_file means the bytes were hashed from a file already on disk with no retrieval record -- the hash is trustworthy, the provenance before it is not.';


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.documents_id_seq OWNED BY public.documents.id;


--
-- Name: event_attestations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_attestations (
    id bigint NOT NULL,
    asserted_event_id bigint NOT NULL,
    claim_id bigint NOT NULL,
    stated_start date,
    stated_end date,
    stated_precision text,
    stated_date_text text,
    attestation_type text NOT NULL,
    derives_from_attestation_id bigint,
    text_similarity numeric(4,3),
    figure_data_point_id bigint
);


--
-- Name: event_attestations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_attestations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_attestations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_attestations_id_seq OWNED BY public.event_attestations.id;


--
-- Name: event_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_items (
    id integer NOT NULL,
    event_id integer NOT NULL,
    legistar_item_id integer,
    matter_id integer,
    sequence integer,
    title text,
    action_text text,
    passed_flag boolean,
    on_consent_agenda boolean DEFAULT false,
    pulled_from_consent boolean DEFAULT false,
    mover_person_id integer,
    seconder_person_id integer
);


--
-- Name: event_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_items_id_seq OWNED BY public.event_items.id;


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id integer NOT NULL,
    body_id integer NOT NULL,
    legistar_event_id integer,
    legistar_guid text,
    legistar_meeting_id integer,
    event_date date NOT NULL,
    start_time time without time zone,
    end_time time without time zone,
    location text,
    meeting_kind text,
    video_available boolean,
    video_absent_reason text,
    agenda_url text,
    agenda_html_url text,
    minutes_url text,
    minutes_html_url text,
    ecomment_url text
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: figure_data_points; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.figure_data_points (
    id bigint NOT NULL,
    figure_id integer NOT NULL,
    label text NOT NULL,
    value_text text NOT NULL,
    value_numeric numeric,
    unit text,
    model_confidence text,
    period_start date,
    period_end date,
    period_is_partial boolean DEFAULT false NOT NULL,
    CONSTRAINT figure_data_points_value_text_check CHECK ((length(TRIM(BOTH FROM value_text)) > 0))
);


--
-- Name: figure_data_points_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.figure_data_points_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: figure_data_points_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.figure_data_points_id_seq OWNED BY public.figure_data_points.id;


--
-- Name: fiscal_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fiscal_references (
    id bigint NOT NULL,
    subject_id integer,
    claim_id bigint NOT NULL,
    amount_low numeric(14,2),
    amount_high numeric(14,2),
    currency character(3) DEFAULT 'USD'::bpchar,
    fiscal_year text,
    funding_source text,
    funding_instrument text,
    recurrence text,
    purpose text,
    awarding_org_id integer,
    award_status text DEFAULT 'unknown'::text NOT NULL,
    status_as_of date,
    status_change_reason text,
    source_type text NOT NULL,
    verbatim text NOT NULL,
    funder_name_text text,
    program_id integer,
    direction text,
    CONSTRAINT fiscal_references_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: COLUMN fiscal_references.funder_name_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.fiscal_references.funder_name_text IS 'The funder as the document names it, verbatim. Never normalised. Populated even when awarding_org_id could not be resolved -- a name here with a NULL id is a registry gap, not an unfunded award.';


--
-- Name: COLUMN fiscal_references.program_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.fiscal_references.program_id IS 'The named program the money came under -- EECBG, SEMCOG Carbon Reduction Program. NOT the same as awarding_org_id (the body) or funding_instrument (the category). A program with a NULL administering_org_id is a research question, not a bad row.';


--
-- Name: COLUMN fiscal_references.direction; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.fiscal_references.direction IS 'Which way the money moved, from the claim''s own words. NEVER sum across directions. `unknown` means the text did not say and is not a synonym for `received`.';


--
-- Name: fiscal_references_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fiscal_references_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fiscal_references_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fiscal_references_id_seq OWNED BY public.fiscal_references.id;


--
-- Name: footnote_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.footnote_references (
    id integer NOT NULL,
    footnote_id integer NOT NULL,
    document_section_id integer NOT NULL,
    char_at integer,
    marker_evidence text
);


--
-- Name: COLUMN footnote_references.marker_evidence; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.footnote_references.marker_evidence IS 'How the marker was established. On an OCR-only document it is never simply READ: the superscript arrives as a letter, an apostrophe, or nothing, and the digit comes from a second independent read.';


--
-- Name: footnote_references_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.footnote_references_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: footnote_references_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.footnote_references_id_seq OWNED BY public.footnote_references.id;


--
-- Name: footnotes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.footnotes (
    id integer NOT NULL,
    document_id integer NOT NULL,
    number integer NOT NULL,
    body_text text NOT NULL,
    page_no integer,
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    document_section_id integer,
    contact_person_id integer
);


--
-- Name: COLUMN footnotes.contact_person_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.footnotes.contact_person_id IS 'The person this footnote names as a contact, resolved to the person registry. NULL when the name could not be resolved to exactly one person -- a footnote attributed to the wrong officer is worse than one attributed to nobody.';


--
-- Name: footnotes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.footnotes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: footnotes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.footnotes_id_seq OWNED BY public.footnotes.id;


--
-- Name: framework_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.framework_categories (
    id integer NOT NULL,
    framework_id integer NOT NULL,
    code text,
    name text NOT NULL,
    parent_category_id integer,
    sequence integer
);


--
-- Name: framework_categories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.framework_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: framework_categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.framework_categories_id_seq OWNED BY public.framework_categories.id;


--
-- Name: frameworks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.frameworks (
    id integer NOT NULL,
    defining_document_id integer,
    jurisdiction_id integer,
    name text NOT NULL,
    valid_from date,
    valid_to date
);


--
-- Name: frameworks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.frameworks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: frameworks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.frameworks_id_seq OWNED BY public.frameworks.id;


--
-- Name: funding_program_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.funding_program_aliases (
    id integer NOT NULL,
    program_id integer NOT NULL,
    alias text NOT NULL,
    alias_type text
);


--
-- Name: funding_program_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.funding_program_aliases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: funding_program_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.funding_program_aliases_id_seq OWNED BY public.funding_program_aliases.id;


--
-- Name: funding_programs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.funding_programs (
    id integer NOT NULL,
    name text NOT NULL,
    administering_org_id integer,
    parent_program_id integer,
    abbreviation text,
    description text,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT funding_programs_check CHECK (((parent_program_id IS NULL) OR (parent_program_id <> id)))
);


--
-- Name: funding_programs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.funding_programs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: funding_programs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.funding_programs_id_seq OWNED BY public.funding_programs.id;


--
-- Name: issue_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issue_aliases (
    id integer NOT NULL,
    issue_id integer NOT NULL,
    alias text NOT NULL
);


--
-- Name: issue_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.issue_aliases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: issue_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.issue_aliases_id_seq OWNED BY public.issue_aliases.id;


--
-- Name: issues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issues (
    id integer NOT NULL,
    canonical_name text NOT NULL,
    description text,
    topic_id integer,
    named_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: issues_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.issues_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: issues_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.issues_id_seq OWNED BY public.issues.id;


--
-- Name: jurisdiction_attributes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jurisdiction_attributes (
    id integer NOT NULL,
    jurisdiction_id integer NOT NULL,
    attribute text NOT NULL,
    value text NOT NULL,
    valid_from date NOT NULL,
    valid_to date,
    source text NOT NULL
);


--
-- Name: jurisdiction_attributes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.jurisdiction_attributes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: jurisdiction_attributes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.jurisdiction_attributes_id_seq OWNED BY public.jurisdiction_attributes.id;


--
-- Name: jurisdiction_citations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jurisdiction_citations (
    id bigint NOT NULL,
    claim_id bigint NOT NULL,
    cited_jurisdiction text NOT NULL,
    cited_ocd_id text,
    cited_as text NOT NULL,
    claimed_lesson text,
    comparability_disputed_by integer,
    dispute_basis text,
    verbatim text NOT NULL,
    CONSTRAINT jurisdiction_citations_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: jurisdiction_citations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.jurisdiction_citations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: jurisdiction_citations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.jurisdiction_citations_id_seq OWNED BY public.jurisdiction_citations.id;


--
-- Name: jurisdictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jurisdictions (
    id integer NOT NULL,
    ocd_division_id text NOT NULL,
    name text NOT NULL,
    state character(2) NOT NULL,
    legistar_client text,
    home_rule boolean,
    government_form text,
    utility_governance text,
    fiscal_year_start_month smallint,
    fiscal_year_labeled_by_end_year boolean DEFAULT true,
    notes text,
    CONSTRAINT jurisdictions_fiscal_year_start_month_check CHECK (((fiscal_year_start_month >= 1) AND (fiscal_year_start_month <= 12)))
);


--
-- Name: jurisdictions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.jurisdictions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: jurisdictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.jurisdictions_id_seq OWNED BY public.jurisdictions.id;


--
-- Name: orgs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orgs (
    id integer NOT NULL,
    name text NOT NULL,
    org_type text NOT NULL,
    ein text,
    jurisdiction_id integer,
    notes text,
    merged_into_id integer,
    merged_by text,
    merged_at timestamp with time zone,
    merge_note text,
    CONSTRAINT orgs_merge_attributed CHECK (((merged_into_id IS NULL) OR ((merged_by IS NOT NULL) AND (merged_at IS NOT NULL)))),
    CONSTRAINT orgs_merge_not_self CHECK (((merged_into_id IS NULL) OR (merged_into_id <> id)))
);


--
-- Name: live_orgs; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.live_orgs AS
 SELECT id,
    name,
    org_type,
    ein,
    jurisdiction_id,
    notes,
    merged_into_id,
    merged_by,
    merged_at,
    merge_note
   FROM public.orgs
  WHERE (merged_into_id IS NULL);


--
-- Name: subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subjects (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    jurisdiction_id integer,
    parent_subject_id integer,
    topic_id integer,
    wiki_slug text,
    formal_objective_achieved text,
    collateral_gains text,
    outcome_notes text,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    subject_kind text,
    merged_into_id integer,
    merged_by text,
    merged_at timestamp with time zone,
    merge_note text,
    CONSTRAINT subjects_check CHECK (((parent_subject_id IS NULL) OR (parent_subject_id <> id))),
    CONSTRAINT subjects_merge_attributed CHECK (((merged_into_id IS NULL) OR ((merged_by IS NOT NULL) AND (merged_at IS NOT NULL)))),
    CONSTRAINT subjects_merge_not_self CHECK (((merged_into_id IS NULL) OR (merged_into_id <> id)))
);


--
-- Name: COLUMN subjects.subject_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.subjects.subject_kind IS 'What sort of thing this is. Without it, telling a strategy from an initiative from a neighbourhood needs a walk up parent_subject_id or a guess from wiki_slug.';


--
-- Name: COLUMN subjects.merged_into_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.subjects.merged_into_id IS 'This subject was found to be the same programme as another. Rows have been repointed to the survivor; this one is kept as a tombstone so pipeline/initiatives.py still resolves the wiki page that created it, instead of recreating the duplicate on the next re-seed.';


--
-- Name: live_subjects; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.live_subjects AS
 SELECT id,
    name,
    description,
    jurisdiction_id,
    parent_subject_id,
    topic_id,
    wiki_slug,
    formal_objective_achieved,
    collateral_gains,
    outcome_notes,
    created_by,
    created_at,
    subject_kind,
    merged_into_id,
    merged_by,
    merged_at,
    merge_note
   FROM public.subjects
  WHERE (merged_into_id IS NULL);


--
-- Name: matter_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.matter_versions (
    id integer NOT NULL,
    matter_id integer NOT NULL,
    version_label text,
    retrieved_at timestamp with time zone NOT NULL,
    body_text text,
    text_hash text
);


--
-- Name: matter_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.matter_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: matter_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.matter_versions_id_seq OWNED BY public.matter_versions.id;


--
-- Name: matters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.matters (
    id integer NOT NULL,
    jurisdiction_id integer NOT NULL,
    legistar_matter_id integer,
    venue_type text DEFAULT 'municipal_legislative'::text NOT NULL,
    venue_jurisdiction_id integer,
    external_identifier text,
    file_number text,
    title text,
    matter_type text,
    status text,
    intro_date date,
    passed_date date,
    enactment_number text
);


--
-- Name: matters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.matters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: matters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.matters_id_seq OWNED BY public.matters.id;


--
-- Name: media_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_assets (
    id integer NOT NULL,
    event_id integer NOT NULL,
    host text NOT NULL,
    external_id text,
    url text NOT NULL,
    duration_seconds integer,
    has_index_points boolean DEFAULT false,
    captions_available boolean,
    asr_model text,
    asr_completed_at timestamp with time zone,
    host_title text,
    host_upload_date date,
    title_stated_date date,
    date_verification text,
    discovered_via text
);


--
-- Name: media_assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.media_assets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: media_assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.media_assets_id_seq OWNED BY public.media_assets.id;


--
-- Name: memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memberships (
    id integer NOT NULL,
    person_id integer NOT NULL,
    post_id integer,
    body_id integer NOT NULL,
    role text,
    valid_from date NOT NULL,
    valid_to date,
    source text
);


--
-- Name: memberships_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memberships_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memberships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memberships_id_seq OWNED BY public.memberships.id;


--
-- Name: org_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.org_aliases (
    id integer NOT NULL,
    org_id integer NOT NULL,
    alias text NOT NULL,
    alias_type text
);


--
-- Name: TABLE org_aliases; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.org_aliases IS 'Other names an organisation is published under, including the name of any org merged into it. resolve_orgs matches claim text against these as well as the canonical name, so a merge does not cost the store a name that documents actually print.';


--
-- Name: org_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.org_aliases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: org_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.org_aliases_id_seq OWNED BY public.org_aliases.id;


--
-- Name: orgs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.orgs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: orgs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.orgs_id_seq OWNED BY public.orgs.id;


--
-- Name: page_manifests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_manifests (
    page_id integer NOT NULL,
    claim_id bigint NOT NULL
);


--
-- Name: page_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_versions (
    id integer NOT NULL,
    page_id integer NOT NULL,
    content_hash text NOT NULL,
    body_markdown text,
    rendered_at timestamp with time zone NOT NULL
);


--
-- Name: page_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.page_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: page_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.page_versions_id_seq OWNED BY public.page_versions.id;


--
-- Name: pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pages (
    id integer NOT NULL,
    page_type text NOT NULL,
    entity_id integer NOT NULL,
    slug text NOT NULL,
    content_hash text NOT NULL,
    body_markdown text,
    prompt_version text NOT NULL,
    model_version text NOT NULL,
    rendered_at timestamp with time zone,
    is_dirty boolean DEFAULT true,
    embedding public.vector(1536),
    embedding_model text
);


--
-- Name: pages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pages_id_seq OWNED BY public.pages.id;


--
-- Name: person_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.person_aliases (
    id integer NOT NULL,
    person_id integer NOT NULL,
    alias text NOT NULL,
    alias_type text
);


--
-- Name: person_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.person_aliases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: person_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.person_aliases_id_seq OWNED BY public.person_aliases.id;


--
-- Name: person_voiceprints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.person_voiceprints (
    id integer NOT NULL,
    person_id integer NOT NULL,
    embedding public.vector(256),
    embedding_model text NOT NULL,
    sample_count integer DEFAULT 1 NOT NULL,
    enrolled_from text NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    enrolled_from_media text[],
    last_confirmed_by text
);


--
-- Name: person_voiceprints_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.person_voiceprints_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: person_voiceprints_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.person_voiceprints_id_seq OWNED BY public.person_voiceprints.id;


--
-- Name: persons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.persons (
    id integer NOT NULL,
    legistar_person_id integer,
    full_name text NOT NULL,
    sort_name text,
    is_public_figure boolean DEFAULT false NOT NULL,
    notes text
);


--
-- Name: persons_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.persons_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: persons_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.persons_id_seq OWNED BY public.persons.id;


--
-- Name: posts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posts (
    id integer NOT NULL,
    body_id integer NOT NULL,
    label text NOT NULL,
    role text
);


--
-- Name: posts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.posts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: posts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.posts_id_seq OWNED BY public.posts.id;


--
-- Name: program_diffusion; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.program_diffusion (
    id bigint NOT NULL,
    subject_id integer NOT NULL,
    adopting_jurisdiction text NOT NULL,
    adopting_ocd_id text,
    relation text NOT NULL,
    occurred_start date,
    occurred_precision text,
    claim_id bigint,
    verbatim text NOT NULL,
    CONSTRAINT program_diffusion_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: program_diffusion_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.program_diffusion_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: program_diffusion_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.program_diffusion_id_seq OWNED BY public.program_diffusion.id;


--
-- Name: quantities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quantities (
    id bigint NOT NULL,
    claim_id bigint NOT NULL,
    subject_id integer,
    value_low numeric,
    value_high numeric,
    bound text,
    unit text NOT NULL,
    unit_basis text,
    measure text NOT NULL,
    scope_note text,
    as_of_date date,
    as_of_precision text,
    is_projection boolean DEFAULT false NOT NULL,
    source_type text,
    verbatim text NOT NULL,
    CONSTRAINT quantities_verbatim_check CHECK ((length(TRIM(BOTH FROM verbatim)) > 0))
);


--
-- Name: COLUMN quantities.unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.quantities.unit IS 'The DIMENSION of the measurement, from a closed vocabulary. This is what you GROUP BY. It must never assert more than the text: bare tons stay metric_tons, because "tons of material" diverted from landfill is not CO2e.';


--
-- Name: COLUMN quantities.unit_basis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.quantities.unit_basis IS 'WHAT WAS COUNTED or what a percentage is OF, verbatim and never normalised: "air quality monitors", "Direct Current Fast Chargers (DCFCs)". This is the detail that makes a row worth reading; `unit` is the part that makes rows comparable.';


--
-- Name: quantities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.quantities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: quantities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.quantities_id_seq OWNED BY public.quantities.id;


--
-- Name: research_questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.research_questions (
    id bigint NOT NULL,
    question text NOT NULL,
    subject_id integer,
    issue_id integer,
    origin text NOT NULL,
    origin_claim_id bigint,
    priority integer DEFAULT 5 NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    promoted_by text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: research_questions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.research_questions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: research_questions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.research_questions_id_seq OWNED BY public.research_questions.id;


--
-- Name: review_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_queue (
    id bigint NOT NULL,
    entity_table text NOT NULL,
    entity_id bigint NOT NULL,
    reason text NOT NULL,
    priority integer DEFAULT 5 NOT NULL,
    assigned_to text,
    status text DEFAULT 'pending'::text,
    resolution text,
    minutes_spent integer,
    created_at timestamp with time zone DEFAULT now(),
    resolved_at timestamp with time zone
);


--
-- Name: review_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.review_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: review_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.review_queue_id_seq OWNED BY public.review_queue.id;


--
-- Name: segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segments (
    id integer NOT NULL,
    event_id integer NOT NULL,
    event_item_id integer,
    subject_id integer,
    sequence integer NOT NULL,
    section_topic text,
    start_ms integer NOT NULL,
    end_ms integer NOT NULL,
    segment_kind text NOT NULL,
    extraction_tier character(1) DEFAULT 'C'::bpchar NOT NULL,
    tier_assigned_by text,
    summary text,
    agenda_deviation_note text,
    deviation_kind text,
    CONSTRAINT segments_check CHECK ((end_ms >= start_ms)),
    CONSTRAINT segments_extraction_tier_check CHECK ((extraction_tier = ANY (ARRAY['A'::bpchar, 'B'::bpchar, 'C'::bpchar])))
);


--
-- Name: segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.segments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.segments_id_seq OWNED BY public.segments.id;


--
-- Name: source_search_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_search_log (
    id bigint NOT NULL,
    subject_id integer,
    doc_type text NOT NULL,
    venue text,
    searched_by text NOT NULL,
    searched_at timestamp with time zone NOT NULL,
    query_used text,
    outcome text NOT NULL,
    coverage_note text
);


--
-- Name: source_search_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_search_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: source_search_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.source_search_log_id_seq OWNED BY public.source_search_log.id;


--
-- Name: source_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_targets (
    id bigint NOT NULL,
    research_question_id bigint,
    target_description text NOT NULL,
    doc_type text,
    venue text,
    expected_url text,
    status text DEFAULT 'identified'::text NOT NULL,
    resolved_document_id integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: source_targets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_targets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: source_targets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.source_targets_id_seq OWNED BY public.source_targets.id;


--
-- Name: subject_aliases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject_aliases (
    id integer NOT NULL,
    subject_id integer NOT NULL,
    alias text NOT NULL,
    alias_type text
);


--
-- Name: subject_aliases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subject_aliases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subject_aliases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subject_aliases_id_seq OWNED BY public.subject_aliases.id;


--
-- Name: subject_framework_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject_framework_categories (
    subject_id integer NOT NULL,
    category_id integer NOT NULL,
    assigned_by text NOT NULL
);


--
-- Name: subject_matters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject_matters (
    subject_id integer NOT NULL,
    matter_id integer NOT NULL,
    confidence numeric(3,2),
    assigned_by text NOT NULL,
    assigned_at timestamp with time zone DEFAULT now()
);


--
-- Name: subject_places; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject_places (
    subject_id integer NOT NULL,
    place_subject_id integer NOT NULL,
    claim_id bigint,
    assigned_by text NOT NULL,
    assigned_at timestamp with time zone DEFAULT now(),
    CONSTRAINT subject_places_check CHECK ((subject_id <> place_subject_id))
);


--
-- Name: subjects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subjects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subjects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subjects_id_seq OWNED BY public.subjects.id;


--
-- Name: topics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.topics (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    parent_topic_id integer,
    named_by text NOT NULL
);


--
-- Name: topics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.topics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: topics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.topics_id_seq OWNED BY public.topics.id;


--
-- Name: utterances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.utterances (
    id bigint NOT NULL,
    segment_id integer NOT NULL,
    media_asset_id integer NOT NULL,
    sequence integer NOT NULL,
    person_id integer,
    speaker_label text,
    actor_capacity text,
    org_id integer,
    start_ms integer NOT NULL,
    end_ms integer NOT NULL,
    duration_ms integer GENERATED ALWAYS AS ((end_ms - start_ms)) STORED,
    text text NOT NULL,
    is_substantive boolean,
    responds_to_utterance_id bigint,
    asr_confidence numeric(3,2),
    date_validation_flag boolean DEFAULT false,
    speaker_id_method text,
    CONSTRAINT utterances_check CHECK ((end_ms >= start_ms))
);


--
-- Name: utterances_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.utterances_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: utterances_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.utterances_id_seq OWNED BY public.utterances.id;


--
-- Name: v_argument_propagation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_argument_propagation AS
 SELECT a.id AS argument_id,
    a.canonical_name,
    c.source_type,
    c.actor_capacity,
    c.speech_act,
    COALESCE(c.asserted_start, e.event_date, d.published_date, d.covers_period_end) AS occurred_on,
    p.full_name AS actor,
    c.id AS claim_id,
    cr.verbatim
   FROM (((((((public.arguments a
     JOIN public.claim_reasons cr ON ((cr.argument_id = a.id)))
     JOIN public.claims c ON ((c.id = cr.claim_id)))
     LEFT JOIN public.persons p ON ((p.id = c.actor_id)))
     LEFT JOIN public.utterances u ON ((u.id = c.utterance_id)))
     LEFT JOIN public.segments s ON ((s.id = u.segment_id)))
     LEFT JOIN public.events e ON ((e.id = s.event_id)))
     LEFT JOIN public.documents d ON ((d.id = c.document_id)))
  ORDER BY a.id, COALESCE(c.asserted_start, e.event_date, d.published_date, d.covers_period_end);


--
-- Name: v_dark_matter_corpus; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_dark_matter_corpus AS
 SELECT c.subject_id,
    c.issue_id,
    i.canonical_name,
    count(*) AS public_claims,
    ( SELECT array_agg(DISTINCT ssl.doc_type) AS array_agg
           FROM public.source_search_log ssl
          WHERE (ssl.subject_id = c.subject_id)) AS searched_types
   FROM (public.claims c
     JOIN public.issues i ON ((i.id = c.issue_id)))
  WHERE ((c.source_type = ANY (ARRAY['video_utterance'::text, 'ecomment'::text, 'correspondence'::text])) AND (c.actor_capacity = ANY (ARRAY['public'::text, 'organizational_rep'::text])) AND (NOT (EXISTS ( SELECT 1
           FROM public.claims c2
          WHERE ((c2.issue_id = c.issue_id) AND (c2.subject_id = c.subject_id) AND (c2.source_type = ANY (ARRAY['staff_report'::text, 'memo'::text, 'minutes'::text, 'plan'::text, 'annual_report'::text])))))))
  GROUP BY c.subject_id, c.issue_id, i.canonical_name
  ORDER BY (count(*)) DESC;


--
-- Name: v_dark_matter_record; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_dark_matter_record AS
 SELECT c.id AS claim_id,
    c.subject_id,
    s.name AS subject_name,
    c."position",
    c.asserted_start,
    c.source_type,
    c.verbatim
   FROM (public.claims c
     LEFT JOIN public.subjects s ON ((s.id = c.subject_id)))
  WHERE c.outcome_without_mechanism
  ORDER BY c.asserted_start;


--
-- Name: vocabulary_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vocabulary_proposals (
    id bigint NOT NULL,
    vocabulary text NOT NULL,
    proposed_term text NOT NULL,
    written_as text,
    entity_table text NOT NULL,
    entity_id bigint NOT NULL,
    rationale text,
    example_verbatim text,
    occurrences integer DEFAULT 1 NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    resolved_by text,
    resolved_at timestamp with time zone
);


--
-- Name: v_entities_with_pending_vocab; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_entities_with_pending_vocab AS
 SELECT entity_table,
    entity_id,
    vocabulary,
    proposed_term,
    written_as,
    occurrences,
    first_seen_at
   FROM public.vocabulary_proposals vp
  WHERE (status = 'pending'::text)
  ORDER BY occurrences DESC;


--
-- Name: v_event_documents; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_event_documents AS
 SELECT e.id AS event_id,
    b.name AS body_name,
    e.event_date,
    e.legistar_event_id,
    e.legistar_meeting_id,
    COALESCE(e.agenda_html_url, e.agenda_url) AS agenda_best_url,
        CASE
            WHEN (e.agenda_html_url IS NOT NULL) THEN 'html'::text
            WHEN (e.agenda_url IS NOT NULL) THEN 'pdf'::text
            ELSE NULL::text
        END AS agenda_format,
    COALESCE(e.minutes_html_url, e.minutes_url) AS minutes_best_url,
        CASE
            WHEN (e.minutes_html_url IS NOT NULL) THEN 'html'::text
            WHEN (e.minutes_url IS NOT NULL) THEN 'pdf'::text
            ELSE NULL::text
        END AS minutes_format,
    ((COALESCE(e.agenda_html_url, e.agenda_url) IS NOT NULL) OR (COALESCE(e.minutes_html_url, e.minutes_url) IS NOT NULL)) AS segmentable,
    m.external_id AS youtube_id,
    m.date_verification
   FROM ((public.events e
     JOIN public.bodies b ON ((b.id = e.body_id)))
     LEFT JOIN public.media_assets m ON ((m.event_id = e.id)));


--
-- Name: v_load_bearing_claims; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_load_bearing_claims AS
 SELECT c.id,
    c."position",
    c.evidence_grade,
    count(pm.page_id) AS page_count
   FROM (public.claims c
     JOIN public.page_manifests pm ON ((pm.claim_id = c.id)))
  WHERE (c.reviewed_by IS NULL)
  GROUP BY c.id, c."position", c.evidence_grade
 HAVING (count(pm.page_id) > 1)
  ORDER BY (count(pm.page_id)) DESC;


--
-- Name: v_meeting_recordings; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_meeting_recordings AS
 SELECT DISTINCT ON (m.event_id) m.event_id,
    e.event_date,
    b.name AS body_name,
    m.id AS media_asset_id,
    m.host,
    m.external_id,
    m.url,
    m.duration_seconds,
    m.host_title,
    m.host_upload_date,
    m.title_stated_date,
    m.date_verification,
    m.discovered_via,
    m.asr_model,
    m.asr_completed_at,
    (count(*) OVER (PARTITION BY m.event_id) - 1) AS others
   FROM ((public.media_assets m
     JOIN public.events e ON ((e.id = m.event_id)))
     JOIN public.bodies b ON ((b.id = e.body_id)))
  ORDER BY m.event_id, m.duration_seconds DESC NULLS LAST, m.id;


--
-- Name: VIEW v_meeting_recordings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_meeting_recordings IS 'One recording per event: the longest. Guards against duplicate and aborted-stream uploads (2026-01-13 has four, three of them under a minute). Read this, not media_assets, when you need "the video for this meeting". `others` counts the additional assets on the same event.';


--
-- Name: v_money_by_direction; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_money_by_direction AS
 SELECT COALESCE(direction, 'unknown'::text) AS direction,
    count(*) AS refs,
    sum(amount_low) AS total_low,
    min(amount_low) AS smallest,
    max(amount_low) AS largest
   FROM public.fiscal_references f
  GROUP BY COALESCE(direction, 'unknown'::text);


--
-- Name: v_numeric_drift; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_numeric_drift AS
 SELECT q.subject_id,
    s.name AS subject_name,
    q.measure,
    q.unit,
    count(DISTINCT q.value_low) AS distinct_values,
    array_agg(DISTINCT q.value_low ORDER BY q.value_low) AS "values",
    array_agg(DISTINCT q.scope_note) AS scopes,
    array_agg(DISTINCT q.source_type) AS source_types
   FROM (public.quantities q
     LEFT JOIN public.subjects s ON ((s.id = q.subject_id)))
  GROUP BY q.subject_id, s.name, q.measure, q.unit
 HAVING (count(DISTINCT q.value_low) > 1);


--
-- Name: v_open_commitments; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_open_commitments AS
 SELECT id,
    subject_id,
    committed_by_person_id,
    committed_by_org_id,
    commitment_text,
    due_date,
    due_description,
    status,
    fulfilled_by_document_id,
    fulfilled_by_event_id,
    source_type,
    utterance_id,
    document_id,
    verbatim,
    (CURRENT_DATE - due_date) AS days_overdue
   FROM public.commitments cm
  WHERE ((status = 'open'::text) AND (fulfilled_by_document_id IS NULL))
  ORDER BY due_date;


--
-- Name: votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.votes (
    id integer NOT NULL,
    event_item_id integer NOT NULL,
    person_id integer NOT NULL,
    vote_value text NOT NULL
);


--
-- Name: v_person_dossier; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_person_dossier AS
 SELECT id AS person_id,
    full_name,
    is_public_figure,
    legistar_person_id,
    ( SELECT array_agg(a.alias ORDER BY a.alias) AS array_agg
           FROM public.person_aliases a
          WHERE (a.person_id = p.id)) AS aliases,
    ( SELECT count(*) AS count
           FROM public.person_voiceprints v
          WHERE (v.person_id = p.id)) AS voiceprint_count,
    ( SELECT max(v.sample_count) AS max
           FROM public.person_voiceprints v
          WHERE (v.person_id = p.id)) AS voiceprint_samples,
    ( SELECT array_agg(DISTINCT m.m) AS array_agg
           FROM public.person_voiceprints v,
            LATERAL unnest(v.enrolled_from_media) m(m)
          WHERE (v.person_id = p.id)) AS voiceprint_sources,
    ( SELECT array_agg(DISTINCT b.name) AS array_agg
           FROM (public.memberships ms
             JOIN public.bodies b ON ((b.id = ms.body_id)))
          WHERE (ms.person_id = p.id)) AS bodies,
    ( SELECT array_agg(DISTINCT o.name) AS array_agg
           FROM (public.affiliations af
             JOIN public.orgs o ON ((o.id = af.org_id)))
          WHERE (af.person_id = p.id)) AS orgs,
    ( SELECT count(*) AS count
           FROM public.utterances u
          WHERE (u.person_id = p.id)) AS utterances,
    ( SELECT count(DISTINCT u.media_asset_id) AS count
           FROM public.utterances u
          WHERE (u.person_id = p.id)) AS meetings_spoken_in,
    ( SELECT round(((sum(u.duration_ms))::numeric / 60000.0), 1) AS round
           FROM public.utterances u
          WHERE (u.person_id = p.id)) AS minutes_spoken,
    ( SELECT array_agg(DISTINCT u.speaker_id_method) AS array_agg
           FROM public.utterances u
          WHERE ((u.person_id = p.id) AND (u.speaker_id_method IS NOT NULL))) AS id_methods,
    ( SELECT count(*) AS count
           FROM public.votes v
          WHERE (v.person_id = p.id)) AS votes_cast,
    ( SELECT count(*) AS count
           FROM public.event_items ei
          WHERE ((ei.mover_person_id = p.id) OR (ei.seconder_person_id = p.id))) AS motions_moved_or_seconded,
    ( SELECT count(*) AS count
           FROM public.claims c
          WHERE (c.actor_id = p.id)) AS claims,
    ( SELECT count(*) AS count
           FROM public.commitments cm
          WHERE (cm.committed_by_person_id = p.id)) AS commitments_made,
    ( SELECT array_agg(DISTINCT d.doc_type) AS array_agg
           FROM (public.claims c
             JOIN public.documents d ON ((d.id = c.document_id)))
          WHERE (c.actor_id = p.id)) AS claim_source_types
   FROM public.persons p;


--
-- Name: VIEW v_person_dossier; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_person_dossier IS 'Compiled record for one person: identity and aliases, voiceprint enrollment, memberships and affiliations, speaking record with identification methods, legislative record from Legistar, and cross-source claim presence. Feeds page_type = ''person''. Derived — never write to it.';


--
-- Name: v_reversed_funding; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_reversed_funding AS
 SELECT fr.id,
    s.name AS subject_name,
    fr.amount_low,
    fr.amount_high,
    fr.funding_source,
    fr.award_status,
    fr.status_as_of,
    fr.status_change_reason,
    fr.verbatim
   FROM (public.fiscal_references fr
     LEFT JOIN public.subjects s ON ((s.id = fr.subject_id)))
  WHERE (fr.award_status = ANY (ARRAY['on_hold'::text, 'terminated'::text, 'rescinded'::text, 'disputed'::text, 'withdrawn'::text]))
  ORDER BY fr.status_as_of DESC NULLS LAST;


--
-- Name: v_scope_conditions; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_scope_conditions AS
 SELECT 'jurisdiction_citation'::text AS origin,
    jc.dispute_basis AS condition_text,
    jc.cited_jurisdiction AS context,
    jc.verbatim
   FROM public.jurisdiction_citations jc
  WHERE (jc.dispute_basis IS NOT NULL)
UNION ALL
 SELECT 'rejected_alternative'::text AS origin,
    ca.rejection_reason AS condition_text,
    ca.alternative_name AS context,
    ca.verbatim
   FROM public.considered_alternatives ca
  WHERE ((ca.disposition = 'rejected'::text) AND ca.is_scope_condition_candidate);


--
-- Name: v_timeline; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_timeline AS
SELECT
    NULL::bigint AS asserted_event_id,
    NULL::integer AS subject_id,
    NULL::text AS canonical_description,
    NULL::text AS event_class,
    NULL::date AS occurred_start,
    NULL::date AS occurred_end,
    NULL::text AS occurred_precision,
    NULL::bigint AS independent_attestations,
    NULL::bigint AS distinct_source_types,
    NULL::bigint AS total_attestations,
    NULL::boolean AS dates_disagree;


--
-- Name: v_unmet_conditions; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_unmet_conditions AS
 SELECT cc.id,
    cc.condition_text,
    cc.condition_origin,
    cc.target_date,
    c.subject_id,
    s.name AS subject_name,
    c.asserted_start AS stated_on,
    (CURRENT_DATE - cc.target_date) AS days_past_target,
    cc.verbatim
   FROM ((public.claim_conditions cc
     JOIN public.claims c ON ((c.id = cc.claim_id)))
     LEFT JOIN public.subjects s ON ((s.id = c.subject_id)))
  WHERE (cc.is_met <> 'true'::text)
  ORDER BY cc.target_date;


--
-- Name: vocabularies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vocabularies (
    name text NOT NULL,
    description text NOT NULL,
    is_open boolean DEFAULT true NOT NULL,
    fallback_term text
);


--
-- Name: vocabulary_proposals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.vocabulary_proposals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: vocabulary_proposals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.vocabulary_proposals_id_seq OWNED BY public.vocabulary_proposals.id;


--
-- Name: vocabulary_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vocabulary_terms (
    vocabulary text NOT NULL,
    term text NOT NULL,
    description text,
    approved_by text NOT NULL,
    approved_at timestamp with time zone DEFAULT now() NOT NULL,
    deprecated_by_term text
);


--
-- Name: votes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.votes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: votes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.votes_id_seq OWNED BY public.votes.id;


--
-- Name: affiliations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliations ALTER COLUMN id SET DEFAULT nextval('public.affiliations_id_seq'::regclass);


--
-- Name: arguments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.arguments ALTER COLUMN id SET DEFAULT nextval('public.arguments_id_seq'::regclass);


--
-- Name: asserted_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asserted_events ALTER COLUMN id SET DEFAULT nextval('public.asserted_events_id_seq'::regclass);


--
-- Name: barriers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.barriers ALTER COLUMN id SET DEFAULT nextval('public.barriers_id_seq'::regclass);


--
-- Name: bodies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bodies ALTER COLUMN id SET DEFAULT nextval('public.bodies_id_seq'::regclass);


--
-- Name: body_lineage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_lineage ALTER COLUMN id SET DEFAULT nextval('public.body_lineage_id_seq'::regclass);


--
-- Name: claim_conditions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_conditions ALTER COLUMN id SET DEFAULT nextval('public.claim_conditions_id_seq'::regclass);


--
-- Name: claim_prior_references id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_prior_references ALTER COLUMN id SET DEFAULT nextval('public.claim_prior_references_id_seq'::regclass);


--
-- Name: claim_reasons id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reasons ALTER COLUMN id SET DEFAULT nextval('public.claim_reasons_id_seq'::regclass);


--
-- Name: claim_relations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_relations ALTER COLUMN id SET DEFAULT nextval('public.claim_relations_id_seq'::regclass);


--
-- Name: claim_subject_mentions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_subject_mentions ALTER COLUMN id SET DEFAULT nextval('public.claim_subject_mentions_id_seq'::regclass);


--
-- Name: claims id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims ALTER COLUMN id SET DEFAULT nextval('public.claims_id_seq'::regclass);


--
-- Name: coalition_members id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members ALTER COLUMN id SET DEFAULT nextval('public.coalition_members_id_seq'::regclass);


--
-- Name: coalitions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalitions ALTER COLUMN id SET DEFAULT nextval('public.coalitions_id_seq'::regclass);


--
-- Name: commitments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments ALTER COLUMN id SET DEFAULT nextval('public.commitments_id_seq'::regclass);


--
-- Name: considered_alternatives id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.considered_alternatives ALTER COLUMN id SET DEFAULT nextval('public.considered_alternatives_id_seq'::regclass);


--
-- Name: curation_decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curation_decisions ALTER COLUMN id SET DEFAULT nextval('public.curation_decisions_id_seq'::regclass);


--
-- Name: decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions ALTER COLUMN id SET DEFAULT nextval('public.decisions_id_seq'::regclass);


--
-- Name: document_figures id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_figures ALTER COLUMN id SET DEFAULT nextval('public.document_figures_id_seq'::regclass);


--
-- Name: document_links id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_links ALTER COLUMN id SET DEFAULT nextval('public.document_links_id_seq'::regclass);


--
-- Name: document_sections id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_sections ALTER COLUMN id SET DEFAULT nextval('public.document_sections_id_seq'::regclass);


--
-- Name: documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents ALTER COLUMN id SET DEFAULT nextval('public.documents_id_seq'::regclass);


--
-- Name: event_attestations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations ALTER COLUMN id SET DEFAULT nextval('public.event_attestations_id_seq'::regclass);


--
-- Name: event_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_items ALTER COLUMN id SET DEFAULT nextval('public.event_items_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: figure_data_points id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.figure_data_points ALTER COLUMN id SET DEFAULT nextval('public.figure_data_points_id_seq'::regclass);


--
-- Name: fiscal_references id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_references ALTER COLUMN id SET DEFAULT nextval('public.fiscal_references_id_seq'::regclass);


--
-- Name: footnote_references id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnote_references ALTER COLUMN id SET DEFAULT nextval('public.footnote_references_id_seq'::regclass);


--
-- Name: footnotes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnotes ALTER COLUMN id SET DEFAULT nextval('public.footnotes_id_seq'::regclass);


--
-- Name: framework_categories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_categories ALTER COLUMN id SET DEFAULT nextval('public.framework_categories_id_seq'::regclass);


--
-- Name: frameworks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frameworks ALTER COLUMN id SET DEFAULT nextval('public.frameworks_id_seq'::regclass);


--
-- Name: funding_program_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_program_aliases ALTER COLUMN id SET DEFAULT nextval('public.funding_program_aliases_id_seq'::regclass);


--
-- Name: funding_programs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_programs ALTER COLUMN id SET DEFAULT nextval('public.funding_programs_id_seq'::regclass);


--
-- Name: issue_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_aliases ALTER COLUMN id SET DEFAULT nextval('public.issue_aliases_id_seq'::regclass);


--
-- Name: issues id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues ALTER COLUMN id SET DEFAULT nextval('public.issues_id_seq'::regclass);


--
-- Name: jurisdiction_attributes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_attributes ALTER COLUMN id SET DEFAULT nextval('public.jurisdiction_attributes_id_seq'::regclass);


--
-- Name: jurisdiction_citations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_citations ALTER COLUMN id SET DEFAULT nextval('public.jurisdiction_citations_id_seq'::regclass);


--
-- Name: jurisdictions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdictions ALTER COLUMN id SET DEFAULT nextval('public.jurisdictions_id_seq'::regclass);


--
-- Name: matter_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matter_versions ALTER COLUMN id SET DEFAULT nextval('public.matter_versions_id_seq'::regclass);


--
-- Name: matters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matters ALTER COLUMN id SET DEFAULT nextval('public.matters_id_seq'::regclass);


--
-- Name: media_assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets ALTER COLUMN id SET DEFAULT nextval('public.media_assets_id_seq'::regclass);


--
-- Name: memberships id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships ALTER COLUMN id SET DEFAULT nextval('public.memberships_id_seq'::regclass);


--
-- Name: org_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_aliases ALTER COLUMN id SET DEFAULT nextval('public.org_aliases_id_seq'::regclass);


--
-- Name: orgs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orgs ALTER COLUMN id SET DEFAULT nextval('public.orgs_id_seq'::regclass);


--
-- Name: page_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_versions ALTER COLUMN id SET DEFAULT nextval('public.page_versions_id_seq'::regclass);


--
-- Name: pages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages ALTER COLUMN id SET DEFAULT nextval('public.pages_id_seq'::regclass);


--
-- Name: person_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_aliases ALTER COLUMN id SET DEFAULT nextval('public.person_aliases_id_seq'::regclass);


--
-- Name: person_voiceprints id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_voiceprints ALTER COLUMN id SET DEFAULT nextval('public.person_voiceprints_id_seq'::regclass);


--
-- Name: persons id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.persons ALTER COLUMN id SET DEFAULT nextval('public.persons_id_seq'::regclass);


--
-- Name: posts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts ALTER COLUMN id SET DEFAULT nextval('public.posts_id_seq'::regclass);


--
-- Name: program_diffusion id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_diffusion ALTER COLUMN id SET DEFAULT nextval('public.program_diffusion_id_seq'::regclass);


--
-- Name: quantities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quantities ALTER COLUMN id SET DEFAULT nextval('public.quantities_id_seq'::regclass);


--
-- Name: research_questions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_questions ALTER COLUMN id SET DEFAULT nextval('public.research_questions_id_seq'::regclass);


--
-- Name: review_queue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_queue ALTER COLUMN id SET DEFAULT nextval('public.review_queue_id_seq'::regclass);


--
-- Name: segments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments ALTER COLUMN id SET DEFAULT nextval('public.segments_id_seq'::regclass);


--
-- Name: source_search_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_search_log ALTER COLUMN id SET DEFAULT nextval('public.source_search_log_id_seq'::regclass);


--
-- Name: source_targets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_targets ALTER COLUMN id SET DEFAULT nextval('public.source_targets_id_seq'::regclass);


--
-- Name: subject_aliases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_aliases ALTER COLUMN id SET DEFAULT nextval('public.subject_aliases_id_seq'::regclass);


--
-- Name: subjects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects ALTER COLUMN id SET DEFAULT nextval('public.subjects_id_seq'::regclass);


--
-- Name: topics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topics ALTER COLUMN id SET DEFAULT nextval('public.topics_id_seq'::regclass);


--
-- Name: utterances id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances ALTER COLUMN id SET DEFAULT nextval('public.utterances_id_seq'::regclass);


--
-- Name: vocabulary_proposals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabulary_proposals ALTER COLUMN id SET DEFAULT nextval('public.vocabulary_proposals_id_seq'::regclass);


--
-- Name: votes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes ALTER COLUMN id SET DEFAULT nextval('public.votes_id_seq'::regclass);


--
-- Name: affiliations affiliations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliations
    ADD CONSTRAINT affiliations_pkey PRIMARY KEY (id);


--
-- Name: arguments arguments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.arguments
    ADD CONSTRAINT arguments_pkey PRIMARY KEY (id);


--
-- Name: asserted_events asserted_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asserted_events
    ADD CONSTRAINT asserted_events_pkey PRIMARY KEY (id);


--
-- Name: barriers barriers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.barriers
    ADD CONSTRAINT barriers_pkey PRIMARY KEY (id);


--
-- Name: bodies bodies_jurisdiction_id_legistar_body_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bodies
    ADD CONSTRAINT bodies_jurisdiction_id_legistar_body_id_key UNIQUE (jurisdiction_id, legistar_body_id);


--
-- Name: bodies bodies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bodies
    ADD CONSTRAINT bodies_pkey PRIMARY KEY (id);


--
-- Name: body_lineage body_lineage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_lineage
    ADD CONSTRAINT body_lineage_pkey PRIMARY KEY (id);


--
-- Name: body_lineage body_lineage_successor_body_id_predecessor_body_id_relation_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_lineage
    ADD CONSTRAINT body_lineage_successor_body_id_predecessor_body_id_relation_key UNIQUE (successor_body_id, predecessor_body_id, relation);


--
-- Name: claim_conditions claim_conditions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_conditions
    ADD CONSTRAINT claim_conditions_pkey PRIMARY KEY (id);


--
-- Name: claim_prior_references claim_prior_references_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_prior_references
    ADD CONSTRAINT claim_prior_references_pkey PRIMARY KEY (id);


--
-- Name: claim_reasons claim_reasons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reasons
    ADD CONSTRAINT claim_reasons_pkey PRIMARY KEY (id);


--
-- Name: claim_relations claim_relations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_relations
    ADD CONSTRAINT claim_relations_pkey PRIMARY KEY (id);


--
-- Name: claim_subject_mentions claim_subject_mentions_claim_id_subject_id_span_start_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_subject_mentions
    ADD CONSTRAINT claim_subject_mentions_claim_id_subject_id_span_start_key UNIQUE (claim_id, subject_id, span_start);


--
-- Name: claim_subject_mentions claim_subject_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_subject_mentions
    ADD CONSTRAINT claim_subject_mentions_pkey PRIMARY KEY (id);


--
-- Name: claims claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_pkey PRIMARY KEY (id);


--
-- Name: coalition_members coalition_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_pkey PRIMARY KEY (id);


--
-- Name: coalition_members coalition_members_unique_member; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_unique_member UNIQUE NULLS NOT DISTINCT (coalition_id, person_id, org_id, body_id);


--
-- Name: coalitions coalitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalitions
    ADD CONSTRAINT coalitions_pkey PRIMARY KEY (id);


--
-- Name: commitments commitments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_pkey PRIMARY KEY (id);


--
-- Name: considered_alternatives considered_alternatives_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.considered_alternatives
    ADD CONSTRAINT considered_alternatives_pkey PRIMARY KEY (id);


--
-- Name: curation_decisions curation_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curation_decisions
    ADD CONSTRAINT curation_decisions_pkey PRIMARY KEY (id);


--
-- Name: decisions decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_pkey PRIMARY KEY (id);


--
-- Name: document_figures document_figures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_figures
    ADD CONSTRAINT document_figures_pkey PRIMARY KEY (id);


--
-- Name: document_links document_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_links
    ADD CONSTRAINT document_links_pkey PRIMARY KEY (id);


--
-- Name: document_sections document_sections_document_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_sections
    ADD CONSTRAINT document_sections_document_id_sequence_key UNIQUE (document_id, sequence);


--
-- Name: document_sections document_sections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_sections
    ADD CONSTRAINT document_sections_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: event_attestations event_attestations_asserted_event_id_claim_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations
    ADD CONSTRAINT event_attestations_asserted_event_id_claim_id_key UNIQUE (asserted_event_id, claim_id);


--
-- Name: event_attestations event_attestations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations
    ADD CONSTRAINT event_attestations_pkey PRIMARY KEY (id);


--
-- Name: event_items event_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_items
    ADD CONSTRAINT event_items_pkey PRIMARY KEY (id);


--
-- Name: events events_body_id_legistar_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_body_id_legistar_event_id_key UNIQUE (body_id, legistar_event_id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: figure_data_points figure_data_points_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.figure_data_points
    ADD CONSTRAINT figure_data_points_pkey PRIMARY KEY (id);


--
-- Name: fiscal_references fiscal_references_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_references
    ADD CONSTRAINT fiscal_references_pkey PRIMARY KEY (id);


--
-- Name: footnote_references footnote_references_footnote_id_document_section_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnote_references
    ADD CONSTRAINT footnote_references_footnote_id_document_section_id_key UNIQUE (footnote_id, document_section_id);


--
-- Name: footnote_references footnote_references_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnote_references
    ADD CONSTRAINT footnote_references_pkey PRIMARY KEY (id);


--
-- Name: footnotes footnotes_document_id_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnotes
    ADD CONSTRAINT footnotes_document_id_number_key UNIQUE (document_id, number);


--
-- Name: footnotes footnotes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnotes
    ADD CONSTRAINT footnotes_pkey PRIMARY KEY (id);


--
-- Name: framework_categories framework_categories_framework_id_code_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT framework_categories_framework_id_code_name_key UNIQUE (framework_id, code, name);


--
-- Name: framework_categories framework_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT framework_categories_pkey PRIMARY KEY (id);


--
-- Name: frameworks frameworks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frameworks
    ADD CONSTRAINT frameworks_pkey PRIMARY KEY (id);


--
-- Name: funding_program_aliases funding_program_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_program_aliases
    ADD CONSTRAINT funding_program_aliases_pkey PRIMARY KEY (id);


--
-- Name: funding_program_aliases funding_program_aliases_program_id_alias_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_program_aliases
    ADD CONSTRAINT funding_program_aliases_program_id_alias_key UNIQUE (program_id, alias);


--
-- Name: funding_programs funding_programs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_programs
    ADD CONSTRAINT funding_programs_pkey PRIMARY KEY (id);


--
-- Name: issue_aliases issue_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_aliases
    ADD CONSTRAINT issue_aliases_pkey PRIMARY KEY (id);


--
-- Name: issues issues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_pkey PRIMARY KEY (id);


--
-- Name: jurisdiction_attributes jurisdiction_attributes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_attributes
    ADD CONSTRAINT jurisdiction_attributes_pkey PRIMARY KEY (id);


--
-- Name: jurisdiction_citations jurisdiction_citations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_citations
    ADD CONSTRAINT jurisdiction_citations_pkey PRIMARY KEY (id);


--
-- Name: jurisdictions jurisdictions_ocd_division_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdictions
    ADD CONSTRAINT jurisdictions_ocd_division_id_key UNIQUE (ocd_division_id);


--
-- Name: jurisdictions jurisdictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdictions
    ADD CONSTRAINT jurisdictions_pkey PRIMARY KEY (id);


--
-- Name: matter_versions matter_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matter_versions
    ADD CONSTRAINT matter_versions_pkey PRIMARY KEY (id);


--
-- Name: matters matters_jurisdiction_id_legistar_matter_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matters
    ADD CONSTRAINT matters_jurisdiction_id_legistar_matter_id_key UNIQUE (jurisdiction_id, legistar_matter_id);


--
-- Name: matters matters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matters
    ADD CONSTRAINT matters_pkey PRIMARY KEY (id);


--
-- Name: media_assets media_assets_event_id_host_external_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_event_id_host_external_id_key UNIQUE (event_id, host, external_id);


--
-- Name: media_assets media_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_pkey PRIMARY KEY (id);


--
-- Name: memberships memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (id);


--
-- Name: org_aliases org_aliases_org_id_alias_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_aliases
    ADD CONSTRAINT org_aliases_org_id_alias_key UNIQUE (org_id, alias);


--
-- Name: org_aliases org_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_aliases
    ADD CONSTRAINT org_aliases_pkey PRIMARY KEY (id);


--
-- Name: orgs orgs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orgs
    ADD CONSTRAINT orgs_pkey PRIMARY KEY (id);


--
-- Name: page_manifests page_manifests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_manifests
    ADD CONSTRAINT page_manifests_pkey PRIMARY KEY (page_id, claim_id);


--
-- Name: page_versions page_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_versions
    ADD CONSTRAINT page_versions_pkey PRIMARY KEY (id);


--
-- Name: pages pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_pkey PRIMARY KEY (id);


--
-- Name: pages pages_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_slug_key UNIQUE (slug);


--
-- Name: person_aliases person_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_aliases
    ADD CONSTRAINT person_aliases_pkey PRIMARY KEY (id);


--
-- Name: person_voiceprints person_voiceprints_person_model_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_voiceprints
    ADD CONSTRAINT person_voiceprints_person_model_key UNIQUE (person_id, embedding_model);


--
-- Name: person_voiceprints person_voiceprints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_voiceprints
    ADD CONSTRAINT person_voiceprints_pkey PRIMARY KEY (id);


--
-- Name: persons persons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.persons
    ADD CONSTRAINT persons_pkey PRIMARY KEY (id);


--
-- Name: posts posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_pkey PRIMARY KEY (id);


--
-- Name: program_diffusion program_diffusion_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_diffusion
    ADD CONSTRAINT program_diffusion_pkey PRIMARY KEY (id);


--
-- Name: quantities quantities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quantities
    ADD CONSTRAINT quantities_pkey PRIMARY KEY (id);


--
-- Name: research_questions research_questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_questions
    ADD CONSTRAINT research_questions_pkey PRIMARY KEY (id);


--
-- Name: review_queue review_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_queue
    ADD CONSTRAINT review_queue_pkey PRIMARY KEY (id);


--
-- Name: segments segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_pkey PRIMARY KEY (id);


--
-- Name: source_search_log source_search_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_search_log
    ADD CONSTRAINT source_search_log_pkey PRIMARY KEY (id);


--
-- Name: source_targets source_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_targets
    ADD CONSTRAINT source_targets_pkey PRIMARY KEY (id);


--
-- Name: subject_aliases subject_aliases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_aliases
    ADD CONSTRAINT subject_aliases_pkey PRIMARY KEY (id);


--
-- Name: subject_framework_categories subject_framework_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_framework_categories
    ADD CONSTRAINT subject_framework_categories_pkey PRIMARY KEY (subject_id, category_id);


--
-- Name: subject_matters subject_matters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_matters
    ADD CONSTRAINT subject_matters_pkey PRIMARY KEY (subject_id, matter_id);


--
-- Name: subject_places subject_places_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_places
    ADD CONSTRAINT subject_places_pkey PRIMARY KEY (subject_id, place_subject_id);


--
-- Name: subjects subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_pkey PRIMARY KEY (id);


--
-- Name: topics topics_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topics
    ADD CONSTRAINT topics_name_key UNIQUE (name);


--
-- Name: topics topics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topics
    ADD CONSTRAINT topics_pkey PRIMARY KEY (id);


--
-- Name: utterances utterances_media_asset_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_media_asset_id_sequence_key UNIQUE (media_asset_id, sequence);


--
-- Name: utterances utterances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_pkey PRIMARY KEY (id);


--
-- Name: vocabularies vocabularies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabularies
    ADD CONSTRAINT vocabularies_pkey PRIMARY KEY (name);


--
-- Name: vocabulary_proposals vocabulary_proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabulary_proposals
    ADD CONSTRAINT vocabulary_proposals_pkey PRIMARY KEY (id);


--
-- Name: vocabulary_proposals vocabulary_proposals_vocabulary_proposed_term_entity_table_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabulary_proposals
    ADD CONSTRAINT vocabulary_proposals_vocabulary_proposed_term_entity_table_key UNIQUE (vocabulary, proposed_term, entity_table);


--
-- Name: vocabulary_terms vocabulary_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabulary_terms
    ADD CONSTRAINT vocabulary_terms_pkey PRIMARY KEY (vocabulary, term);


--
-- Name: votes votes_event_item_id_person_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_event_item_id_person_id_key UNIQUE (event_item_id, person_id);


--
-- Name: votes votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_pkey PRIMARY KEY (id);


--
-- Name: claim_mentions_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX claim_mentions_claim_idx ON public.claim_subject_mentions USING btree (claim_id);


--
-- Name: claim_mentions_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX claim_mentions_subject_idx ON public.claim_subject_mentions USING btree (subject_id);


--
-- Name: claims_document_section_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX claims_document_section_idx ON public.claims USING btree (document_section_id) WHERE (document_section_id IS NOT NULL);


--
-- Name: document_sections_document_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_sections_document_idx ON public.document_sections USING btree (document_id, sequence);


--
-- Name: document_sections_tier_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_sections_tier_idx ON public.document_sections USING btree (extraction_tier) WHERE (extraction_tier = ANY (ARRAY['A'::bpchar, 'B'::bpchar]));


--
-- Name: document_sections_topic_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_sections_topic_idx ON public.document_sections USING btree (section_topic) WHERE (section_topic IS NOT NULL);


--
-- Name: idx_asserted_events_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asserted_events_subject ON public.asserted_events USING btree (subject_id);


--
-- Name: idx_asserted_events_when; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asserted_events_when ON public.asserted_events USING btree (occurred_start, occurred_end);


--
-- Name: idx_attest_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attest_claim ON public.event_attestations USING btree (claim_id);


--
-- Name: idx_attest_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_attest_event ON public.event_attestations USING btree (asserted_event_id);


--
-- Name: idx_claims_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_actor ON public.claims USING btree (actor_id);


--
-- Name: idx_claims_asserted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_asserted ON public.claims USING btree (asserted_start, asserted_end);


--
-- Name: idx_claims_curation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_curation ON public.claims USING btree (curation_state);


--
-- Name: idx_claims_darkmatter; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_darkmatter ON public.claims USING btree (outcome_without_mechanism) WHERE outcome_without_mechanism;


--
-- Name: idx_claims_grade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_grade ON public.claims USING btree (evidence_grade);


--
-- Name: idx_claims_issue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_issue ON public.claims USING btree (issue_id);


--
-- Name: idx_claims_src; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_src ON public.claims USING btree (source_type);


--
-- Name: idx_claims_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_claims_subject ON public.claims USING btree (subject_id);


--
-- Name: idx_conditions_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conditions_claim ON public.claim_conditions USING btree (claim_id);


--
-- Name: idx_conditions_unmet; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conditions_unmet ON public.claim_conditions USING btree (is_met) WHERE (is_met <> 'true'::text);


--
-- Name: idx_curation_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_curation_pair ON public.curation_decisions USING btree (entity_table, entity_a_id, entity_b_id);


--
-- Name: idx_doclinks_document; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_doclinks_document ON public.document_links USING btree (document_id);


--
-- Name: idx_doclinks_uri; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_doclinks_uri ON public.document_links USING btree (uri);


--
-- Name: idx_documents_pub; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_pub ON public.documents USING btree (published_date);


--
-- Name: idx_documents_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_type ON public.documents USING btree (doc_type);


--
-- Name: idx_event_items_legistar; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_event_items_legistar ON public.event_items USING btree (legistar_item_id) WHERE (legistar_item_id IS NOT NULL);


--
-- Name: idx_events_meeting_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_events_meeting_id ON public.events USING btree (legistar_meeting_id) WHERE (legistar_meeting_id IS NOT NULL);


--
-- Name: idx_figpoints_figure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_figpoints_figure ON public.figure_data_points USING btree (figure_id);


--
-- Name: idx_figures_document; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_figures_document ON public.document_figures USING btree (document_id);


--
-- Name: idx_fiscal_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fiscal_status ON public.fiscal_references USING btree (award_status);


--
-- Name: idx_footnotes_document; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_footnotes_document ON public.footnotes USING btree (document_id);


--
-- Name: idx_funding_programs_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_funding_programs_org ON public.funding_programs USING btree (administering_org_id);


--
-- Name: idx_manifest_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_manifest_claim ON public.page_manifests USING btree (claim_id);


--
-- Name: idx_org_aliases_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_org_aliases_org ON public.org_aliases USING btree (org_id);


--
-- Name: idx_orgs_merged_into; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orgs_merged_into ON public.orgs USING btree (merged_into_id) WHERE (merged_into_id IS NOT NULL);


--
-- Name: idx_persons_legistar; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_persons_legistar ON public.persons USING btree (legistar_person_id) WHERE (legistar_person_id IS NOT NULL);


--
-- Name: idx_quantities_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quantities_subject ON public.quantities USING btree (subject_id, measure);


--
-- Name: idx_reasons_arg; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reasons_arg ON public.claim_reasons USING btree (argument_id);


--
-- Name: idx_reasons_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reasons_claim ON public.claim_reasons USING btree (claim_id);


--
-- Name: idx_reasons_vec; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reasons_vec ON public.claim_reasons USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_rq_reason; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rq_reason ON public.review_queue USING btree (reason, status);


--
-- Name: idx_rq_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rq_status ON public.review_queue USING btree (status, priority);


--
-- Name: idx_search_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_search_subject ON public.source_search_log USING btree (subject_id, doc_type);


--
-- Name: idx_sections_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sections_subject ON public.document_sections USING btree (subject_id);


--
-- Name: idx_subject_places_place; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subject_places_place ON public.subject_places USING btree (place_subject_id);


--
-- Name: idx_subjects_merged_into; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subjects_merged_into ON public.subjects USING btree (merged_into_id) WHERE (merged_into_id IS NOT NULL);


--
-- Name: idx_subjects_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subjects_parent ON public.subjects USING btree (parent_subject_id);


--
-- Name: idx_subjects_topic; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_subjects_topic ON public.subjects USING btree (topic_id);


--
-- Name: idx_utt_person; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_utt_person ON public.utterances USING btree (person_id);


--
-- Name: idx_utt_segment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_utt_segment ON public.utterances USING btree (segment_id);


--
-- Name: idx_utt_subst; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_utt_subst ON public.utterances USING btree (is_substantive) WHERE is_substantive;


--
-- Name: idx_vocab_prop_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vocab_prop_entity ON public.vocabulary_proposals USING btree (entity_table, entity_id);


--
-- Name: idx_vocab_prop_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_vocab_prop_pending ON public.vocabulary_proposals USING btree (status, occurrences DESC);


--
-- Name: v_timeline _RETURN; Type: RULE; Schema: public; Owner: -
--

CREATE OR REPLACE VIEW public.v_timeline AS
 SELECT ae.id AS asserted_event_id,
    ae.subject_id,
    ae.canonical_description,
    ae.event_class,
    ae.occurred_start,
    ae.occurred_end,
    ae.occurred_precision,
    count(*) FILTER (WHERE (ea.attestation_type = ANY (ARRAY['primary_record'::text, 'firsthand'::text, 'secondhand'::text]))) AS independent_attestations,
    count(DISTINCT c.source_type) AS distinct_source_types,
    count(*) AS total_attestations,
    (count(DISTINCT ea.stated_start) > 1) AS dates_disagree
   FROM ((public.asserted_events ae
     JOIN public.event_attestations ea ON ((ea.asserted_event_id = ae.id)))
     JOIN public.claims c ON ((c.id = ea.claim_id)))
  GROUP BY ae.id
  ORDER BY (count(*) FILTER (WHERE (ea.attestation_type = ANY (ARRAY['primary_record'::text, 'firsthand'::text, 'secondhand'::text])))) DESC, ae.occurred_start;


--
-- Name: page_manifests trg_manifest_curation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_manifest_curation BEFORE INSERT ON public.page_manifests FOR EACH ROW EXECUTE FUNCTION public.require_curated_claim();


--
-- Name: orgs trg_org_merge_target; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_org_merge_target BEFORE INSERT OR UPDATE OF merged_into_id ON public.orgs FOR EACH ROW EXECUTE FUNCTION public.enforce_merge_target();


--
-- Name: subjects trg_subject_merge_target; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_subject_merge_target BEFORE INSERT OR UPDATE OF merged_into_id ON public.subjects FOR EACH ROW EXECUTE FUNCTION public.enforce_merge_target();


--
-- Name: asserted_events trg_vocab_aevents; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_aevents BEFORE INSERT OR UPDATE ON public.asserted_events FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('asserted_event_class', 'event_class');


--
-- Name: asserted_events trg_vocab_aevents_prec; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_aevents_prec BEFORE INSERT OR UPDATE ON public.asserted_events FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('date_precision', 'occurred_precision');


--
-- Name: affiliations trg_vocab_affiliations; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_affiliations BEFORE INSERT OR UPDATE ON public.affiliations FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('affiliation_source', 'source');


--
-- Name: considered_alternatives trg_vocab_alt_disp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_alt_disp BEFORE INSERT OR UPDATE ON public.considered_alternatives FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('alternative_disposition', 'disposition');


--
-- Name: considered_alternatives trg_vocab_alt_rej; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_alt_rej BEFORE INSERT OR UPDATE ON public.considered_alternatives FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('rejection_class', 'rejection_class');


--
-- Name: arguments trg_vocab_arguments; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_arguments BEFORE INSERT OR UPDATE ON public.arguments FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('reason_class', 'argument_class');


--
-- Name: event_attestations trg_vocab_attest; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_attest BEFORE INSERT OR UPDATE ON public.event_attestations FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('attestation_type', 'attestation_type');


--
-- Name: barriers trg_vocab_barriers; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_barriers BEFORE INSERT OR UPDATE ON public.barriers FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('barrier_class', 'barrier_class');


--
-- Name: bodies trg_vocab_bodies_auth; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_bodies_auth BEFORE INSERT OR UPDATE ON public.bodies FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('authority_type', 'authority_type');


--
-- Name: bodies trg_vocab_bodies_class; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_bodies_class BEFORE INSERT OR UPDATE ON public.bodies FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('body_classification', 'classification');


--
-- Name: claims trg_vocab_claims_act; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_act BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('speech_act', 'speech_act');


--
-- Name: claims trg_vocab_claims_cal; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_cal BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('calendar_system', 'asserted_calendar');


--
-- Name: claims trg_vocab_claims_cap; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_cap BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('actor_capacity', 'actor_capacity');


--
-- Name: claims trg_vocab_claims_cur; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_cur BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('curation_state', 'curation_state');


--
-- Name: claims trg_vocab_claims_dim; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_dim BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('contested_dimension', 'contested_dimension');


--
-- Name: claims trg_vocab_claims_mod; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_mod BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('modality', 'modality');


--
-- Name: claims trg_vocab_claims_pol; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_pol BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('polarity', 'polarity');


--
-- Name: claims trg_vocab_claims_prec; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_prec BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('date_precision', 'asserted_precision');


--
-- Name: claims trg_vocab_claims_span; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_span BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('span_unit', 'span_unit');


--
-- Name: claims trg_vocab_claims_src; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_claims_src BEFORE INSERT OR UPDATE ON public.claims FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('source_type', 'source_type');


--
-- Name: coalition_members trg_vocab_coalition_role; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_coalition_role BEFORE INSERT OR UPDATE ON public.coalition_members FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('involvement_role', 'role');


--
-- Name: coalitions trg_vocab_coalitions; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_coalitions BEFORE INSERT OR UPDATE ON public.coalitions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('coalition_type', 'coalition_type');


--
-- Name: commitments trg_vocab_commitments; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_commitments BEFORE INSERT OR UPDATE ON public.commitments FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('commitment_status', 'status');


--
-- Name: claim_conditions trg_vocab_cond_met; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_cond_met BEFORE INSERT OR UPDATE ON public.claim_conditions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('is_met', 'is_met');


--
-- Name: claim_conditions trg_vocab_cond_origin; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_cond_origin BEFORE INSERT OR UPDATE ON public.claim_conditions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('condition_origin', 'condition_origin');


--
-- Name: documents trg_vocab_covers_period_source; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_covers_period_source BEFORE INSERT OR UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('covers_period_source', 'covers_period_source');


--
-- Name: curation_decisions trg_vocab_curation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_curation BEFORE INSERT OR UPDATE ON public.curation_decisions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('curation_decision', 'decision');


--
-- Name: decisions trg_vocab_decisions_out; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_decisions_out BEFORE INSERT OR UPDATE ON public.decisions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('decision_outcome', 'outcome');


--
-- Name: decisions trg_vocab_decisions_type; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_decisions_type BEFORE INSERT OR UPDATE ON public.decisions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('decision_type', 'decision_type');


--
-- Name: program_diffusion trg_vocab_diffusion; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_diffusion BEFORE INSERT OR UPDATE ON public.program_diffusion FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('diffusion_relation', 'relation');


--
-- Name: documents trg_vocab_documents; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_documents BEFORE INSERT OR UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('doc_type', 'doc_type');


--
-- Name: documents trg_vocab_documents_snapshot; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_documents_snapshot BEFORE INSERT OR UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('provenance_source', 'snapshot_source');


--
-- Name: events trg_vocab_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_events BEFORE INSERT OR UPDATE ON public.events FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('meeting_kind', 'meeting_kind');


--
-- Name: fiscal_references trg_vocab_fiscal_direction; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_fiscal_direction BEFORE INSERT OR UPDATE ON public.fiscal_references FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('fiscal_direction', 'direction');


--
-- Name: fiscal_references trg_vocab_fiscal_instr; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_fiscal_instr BEFORE INSERT OR UPDATE ON public.fiscal_references FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('funding_instrument', 'funding_instrument');


--
-- Name: fiscal_references trg_vocab_fiscal_rec; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_fiscal_rec BEFORE INSERT OR UPDATE ON public.fiscal_references FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('funding_recurrence', 'recurrence');


--
-- Name: fiscal_references trg_vocab_fiscal_src; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_fiscal_src BEFORE INSERT OR UPDATE ON public.fiscal_references FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('funding_source', 'funding_source');


--
-- Name: fiscal_references trg_vocab_fiscal_stat; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_fiscal_stat BEFORE INSERT OR UPDATE ON public.fiscal_references FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('award_status', 'award_status');


--
-- Name: footnote_references trg_vocab_footnote_ref; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_footnote_ref BEFORE INSERT OR UPDATE ON public.footnote_references FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('marker_evidence', 'marker_evidence');


--
-- Name: funding_program_aliases trg_vocab_funding_alias_type; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_funding_alias_type BEFORE INSERT OR UPDATE ON public.funding_program_aliases FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('alias_type', 'alias_type');


--
-- Name: jurisdiction_citations trg_vocab_jcitations; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_jcitations BEFORE INSERT OR UPDATE ON public.jurisdiction_citations FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('citation_stance', 'cited_as');


--
-- Name: jurisdictions trg_vocab_jurisdictions_gov; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_jurisdictions_gov BEFORE INSERT OR UPDATE ON public.jurisdictions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('government_form', 'government_form');


--
-- Name: jurisdictions trg_vocab_jurisdictions_util; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_jurisdictions_util BEFORE INSERT OR UPDATE ON public.jurisdictions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('utility_governance', 'utility_governance');


--
-- Name: body_lineage trg_vocab_lineage; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_lineage BEFORE INSERT OR UPDATE ON public.body_lineage FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('body_lineage_relation', 'relation');


--
-- Name: matters trg_vocab_matters; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_matters BEFORE INSERT OR UPDATE ON public.matters FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('venue_type', 'venue_type');


--
-- Name: media_assets trg_vocab_media; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_media BEFORE INSERT OR UPDATE ON public.media_assets FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('media_host', 'host');


--
-- Name: media_assets trg_vocab_media_disc; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_media_disc BEFORE INSERT OR UPDATE ON public.media_assets FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('media_discovery_method', 'discovered_via');


--
-- Name: media_assets trg_vocab_media_dv; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_media_dv BEFORE INSERT OR UPDATE ON public.media_assets FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('video_date_verification', 'date_verification');


--
-- Name: claim_subject_mentions trg_vocab_mention_method; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_mention_method BEFORE INSERT OR UPDATE ON public.claim_subject_mentions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('mention_method', 'method');


--
-- Name: figure_data_points trg_vocab_model_confidence; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_model_confidence BEFORE INSERT OR UPDATE ON public.figure_data_points FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('model_confidence', 'model_confidence');


--
-- Name: org_aliases trg_vocab_org_alias_type; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_org_alias_type BEFORE INSERT OR UPDATE ON public.org_aliases FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('alias_type', 'alias_type');


--
-- Name: orgs trg_vocab_orgs; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_orgs BEFORE INSERT OR UPDATE ON public.orgs FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('org_type', 'org_type');


--
-- Name: pages trg_vocab_pages; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_pages BEFORE INSERT OR UPDATE ON public.pages FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('page_type', 'page_type');


--
-- Name: document_sections trg_vocab_parse_confidence; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_parse_confidence BEFORE INSERT OR UPDATE ON public.document_sections FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('parse_confidence', 'parse_confidence');


--
-- Name: person_aliases trg_vocab_person_aliases; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_person_aliases BEFORE INSERT OR UPDATE ON public.person_aliases FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('alias_type', 'alias_type');


--
-- Name: posts trg_vocab_posts; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_posts BEFORE INSERT OR UPDATE ON public.posts FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('post_role', 'role');


--
-- Name: quantities trg_vocab_qty_bound; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_qty_bound BEFORE INSERT OR UPDATE ON public.quantities FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('quantity_bound', 'bound');


--
-- Name: quantities trg_vocab_qty_measure; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_qty_measure BEFORE INSERT OR UPDATE ON public.quantities FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('quantity_measure', 'measure');


--
-- Name: quantities trg_vocab_qty_unit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_qty_unit BEFORE INSERT OR UPDATE ON public.quantities FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('quantity_unit', 'unit');


--
-- Name: claim_reasons trg_vocab_reasons; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_reasons BEFORE INSERT OR UPDATE ON public.claim_reasons FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('reason_class', 'reason_class');


--
-- Name: claim_relations trg_vocab_relations; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_relations BEFORE INSERT OR UPDATE ON public.claim_relations FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('claim_relation', 'relation');


--
-- Name: review_queue trg_vocab_review; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_review BEFORE INSERT OR UPDATE ON public.review_queue FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('review_reason', 'reason');


--
-- Name: research_questions trg_vocab_rq; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_rq BEFORE INSERT OR UPDATE ON public.research_questions FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('research_question_origin', 'origin');


--
-- Name: document_sections trg_vocab_section_topic; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_section_topic BEFORE INSERT OR UPDATE ON public.document_sections FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('section_topic', 'section_topic');


--
-- Name: document_sections trg_vocab_sections_human; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_sections_human BEFORE INSERT OR UPDATE ON public.document_sections FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('human_verdict', 'human_verdict');


--
-- Name: segments trg_vocab_segments_dev; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_segments_dev BEFORE INSERT OR UPDATE ON public.segments FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('deviation_kind', 'deviation_kind');


--
-- Name: segments trg_vocab_segments_kind; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_segments_kind BEFORE INSERT OR UPDATE ON public.segments FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('segment_kind', 'segment_kind');


--
-- Name: source_search_log trg_vocab_ssl_doc; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_ssl_doc BEFORE INSERT OR UPDATE ON public.source_search_log FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('doc_type', 'doc_type');


--
-- Name: source_search_log trg_vocab_ssl_out; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_ssl_out BEFORE INSERT OR UPDATE ON public.source_search_log FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('search_outcome', 'outcome');


--
-- Name: source_targets trg_vocab_st_doc; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_st_doc BEFORE INSERT OR UPDATE ON public.source_targets FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('doc_type', 'doc_type');


--
-- Name: source_targets trg_vocab_st_status; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_st_status BEFORE INSERT OR UPDATE ON public.source_targets FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('source_target_status', 'status');


--
-- Name: subject_aliases trg_vocab_subject_aliases; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_subject_aliases BEFORE INSERT OR UPDATE ON public.subject_aliases FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('alias_type', 'alias_type');


--
-- Name: subjects trg_vocab_subject_kind; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_subject_kind BEFORE INSERT OR UPDATE ON public.subjects FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('subject_kind', 'subject_kind');


--
-- Name: subjects trg_vocab_subjects_gains; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_subjects_gains BEFORE INSERT OR UPDATE ON public.subjects FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('collateral_gains', 'collateral_gains');


--
-- Name: subjects trg_vocab_subjects_obj; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_subjects_obj BEFORE INSERT OR UPDATE ON public.subjects FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('objective_outcome', 'formal_objective_achieved');


--
-- Name: utterances trg_vocab_utt_cap; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_utt_cap BEFORE INSERT OR UPDATE ON public.utterances FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('actor_capacity', 'actor_capacity');


--
-- Name: utterances trg_vocab_utt_sid; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_utt_sid BEFORE INSERT OR UPDATE ON public.utterances FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('speaker_id_method', 'speaker_id_method');


--
-- Name: person_voiceprints trg_vocab_voiceprints; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_voiceprints BEFORE INSERT OR UPDATE ON public.person_voiceprints FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('speaker_id_method', 'enrolled_from');


--
-- Name: votes trg_vocab_votes; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_vocab_votes BEFORE INSERT OR UPDATE ON public.votes FOR EACH ROW EXECUTE FUNCTION public.enforce_vocabulary('vote_value', 'vote_value');


--
-- Name: person_voiceprints trg_voiceprint_privacy; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_voiceprint_privacy BEFORE INSERT OR UPDATE ON public.person_voiceprints FOR EACH ROW EXECUTE FUNCTION public.reject_private_voiceprint();


--
-- Name: affiliations affiliations_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliations
    ADD CONSTRAINT affiliations_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id);


--
-- Name: affiliations affiliations_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.affiliations
    ADD CONSTRAINT affiliations_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id);


--
-- Name: asserted_events asserted_events_legistar_event_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asserted_events
    ADD CONSTRAINT asserted_events_legistar_event_item_id_fkey FOREIGN KEY (legistar_event_item_id) REFERENCES public.event_items(id);


--
-- Name: asserted_events asserted_events_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asserted_events
    ADD CONSTRAINT asserted_events_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: barriers barriers_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.barriers
    ADD CONSTRAINT barriers_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id);


--
-- Name: barriers barriers_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.barriers
    ADD CONSTRAINT barriers_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.issues(id);


--
-- Name: barriers barriers_named_by_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.barriers
    ADD CONSTRAINT barriers_named_by_person_id_fkey FOREIGN KEY (named_by_person_id) REFERENCES public.persons(id);


--
-- Name: barriers barriers_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.barriers
    ADD CONSTRAINT barriers_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: bodies bodies_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bodies
    ADD CONSTRAINT bodies_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: body_lineage body_lineage_predecessor_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_lineage
    ADD CONSTRAINT body_lineage_predecessor_body_id_fkey FOREIGN KEY (predecessor_body_id) REFERENCES public.bodies(id);


--
-- Name: body_lineage body_lineage_successor_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_lineage
    ADD CONSTRAINT body_lineage_successor_body_id_fkey FOREIGN KEY (successor_body_id) REFERENCES public.bodies(id);


--
-- Name: claim_conditions claim_conditions_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_conditions
    ADD CONSTRAINT claim_conditions_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: claim_conditions claim_conditions_resolved_by_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_conditions
    ADD CONSTRAINT claim_conditions_resolved_by_claim_id_fkey FOREIGN KEY (resolved_by_claim_id) REFERENCES public.claims(id);


--
-- Name: claim_prior_references claim_prior_references_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_prior_references
    ADD CONSTRAINT claim_prior_references_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: claim_prior_references claim_prior_references_referenced_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_prior_references
    ADD CONSTRAINT claim_prior_references_referenced_body_id_fkey FOREIGN KEY (referenced_body_id) REFERENCES public.bodies(id);


--
-- Name: claim_prior_references claim_prior_references_referenced_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_prior_references
    ADD CONSTRAINT claim_prior_references_referenced_event_id_fkey FOREIGN KEY (referenced_event_id) REFERENCES public.events(id);


--
-- Name: claim_reasons claim_reasons_argument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reasons
    ADD CONSTRAINT claim_reasons_argument_id_fkey FOREIGN KEY (argument_id) REFERENCES public.arguments(id);


--
-- Name: claim_reasons claim_reasons_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_reasons
    ADD CONSTRAINT claim_reasons_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: claim_relations claim_relations_claim_a_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_relations
    ADD CONSTRAINT claim_relations_claim_a_id_fkey FOREIGN KEY (claim_a_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: claim_relations claim_relations_claim_b_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_relations
    ADD CONSTRAINT claim_relations_claim_b_id_fkey FOREIGN KEY (claim_b_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: claim_subject_mentions claim_subject_mentions_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_subject_mentions
    ADD CONSTRAINT claim_subject_mentions_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: claim_subject_mentions claim_subject_mentions_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claim_subject_mentions
    ADD CONSTRAINT claim_subject_mentions_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: claims claims_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.persons(id);


--
-- Name: claims claims_competing_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_competing_subject_id_fkey FOREIGN KEY (competing_subject_id) REFERENCES public.subjects(id);


--
-- Name: claims claims_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);


--
-- Name: claims claims_document_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_document_section_id_fkey FOREIGN KEY (document_section_id) REFERENCES public.document_sections(id);


--
-- Name: claims claims_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.issues(id);


--
-- Name: claims claims_matter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES public.matters(id);


--
-- Name: claims claims_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id);


--
-- Name: claims claims_reported_by_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_reported_by_document_id_fkey FOREIGN KEY (reported_by_document_id) REFERENCES public.documents(id);


--
-- Name: claims claims_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: claims claims_utterance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.claims
    ADD CONSTRAINT claims_utterance_id_fkey FOREIGN KEY (utterance_id) REFERENCES public.utterances(id);


--
-- Name: coalition_members coalition_members_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_body_id_fkey FOREIGN KEY (body_id) REFERENCES public.bodies(id);


--
-- Name: coalition_members coalition_members_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id);


--
-- Name: coalition_members coalition_members_coalition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_coalition_id_fkey FOREIGN KEY (coalition_id) REFERENCES public.coalitions(id);


--
-- Name: coalition_members coalition_members_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id);


--
-- Name: coalition_members coalition_members_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalition_members
    ADD CONSTRAINT coalition_members_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id);


--
-- Name: coalitions coalitions_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coalitions
    ADD CONSTRAINT coalitions_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: commitments commitments_committed_by_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_committed_by_org_id_fkey FOREIGN KEY (committed_by_org_id) REFERENCES public.orgs(id);


--
-- Name: commitments commitments_committed_by_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_committed_by_person_id_fkey FOREIGN KEY (committed_by_person_id) REFERENCES public.persons(id);


--
-- Name: commitments commitments_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);


--
-- Name: commitments commitments_fulfilled_by_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_fulfilled_by_document_id_fkey FOREIGN KEY (fulfilled_by_document_id) REFERENCES public.documents(id);


--
-- Name: commitments commitments_fulfilled_by_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_fulfilled_by_event_id_fkey FOREIGN KEY (fulfilled_by_event_id) REFERENCES public.events(id);


--
-- Name: commitments commitments_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: commitments commitments_utterance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_utterance_id_fkey FOREIGN KEY (utterance_id) REFERENCES public.utterances(id);


--
-- Name: considered_alternatives considered_alternatives_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.considered_alternatives
    ADD CONSTRAINT considered_alternatives_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id);


--
-- Name: considered_alternatives considered_alternatives_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.considered_alternatives
    ADD CONSTRAINT considered_alternatives_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: considered_alternatives considered_alternatives_superseded_by_alternative_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.considered_alternatives
    ADD CONSTRAINT considered_alternatives_superseded_by_alternative_id_fkey FOREIGN KEY (superseded_by_alternative_id) REFERENCES public.considered_alternatives(id);


--
-- Name: decisions decisions_event_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_event_item_id_fkey FOREIGN KEY (event_item_id) REFERENCES public.event_items(id);


--
-- Name: decisions decisions_mover_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_mover_person_id_fkey FOREIGN KEY (mover_person_id) REFERENCES public.persons(id);


--
-- Name: decisions decisions_seconder_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_seconder_person_id_fkey FOREIGN KEY (seconder_person_id) REFERENCES public.persons(id);


--
-- Name: decisions decisions_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: decisions decisions_utterance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT decisions_utterance_id_fkey FOREIGN KEY (utterance_id) REFERENCES public.utterances(id);


--
-- Name: document_figures document_figures_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_figures
    ADD CONSTRAINT document_figures_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_links document_links_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_links
    ADD CONSTRAINT document_links_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_sections document_sections_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_sections
    ADD CONSTRAINT document_sections_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_sections document_sections_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_sections
    ADD CONSTRAINT document_sections_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: documents documents_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: documents documents_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: documents documents_matter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES public.matters(id);


--
-- Name: event_attestations event_attestations_asserted_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations
    ADD CONSTRAINT event_attestations_asserted_event_id_fkey FOREIGN KEY (asserted_event_id) REFERENCES public.asserted_events(id) ON DELETE CASCADE;


--
-- Name: event_attestations event_attestations_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations
    ADD CONSTRAINT event_attestations_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: event_attestations event_attestations_derives_from_attestation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations
    ADD CONSTRAINT event_attestations_derives_from_attestation_id_fkey FOREIGN KEY (derives_from_attestation_id) REFERENCES public.event_attestations(id);


--
-- Name: event_attestations event_attestations_figure_data_point_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_attestations
    ADD CONSTRAINT event_attestations_figure_data_point_id_fkey FOREIGN KEY (figure_data_point_id) REFERENCES public.figure_data_points(id);


--
-- Name: event_items event_items_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_items
    ADD CONSTRAINT event_items_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: event_items event_items_matter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_items
    ADD CONSTRAINT event_items_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES public.matters(id);


--
-- Name: event_items event_items_mover_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_items
    ADD CONSTRAINT event_items_mover_person_id_fkey FOREIGN KEY (mover_person_id) REFERENCES public.persons(id);


--
-- Name: event_items event_items_seconder_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_items
    ADD CONSTRAINT event_items_seconder_person_id_fkey FOREIGN KEY (seconder_person_id) REFERENCES public.persons(id);


--
-- Name: events events_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_body_id_fkey FOREIGN KEY (body_id) REFERENCES public.bodies(id);


--
-- Name: figure_data_points figure_data_points_figure_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.figure_data_points
    ADD CONSTRAINT figure_data_points_figure_id_fkey FOREIGN KEY (figure_id) REFERENCES public.document_figures(id) ON DELETE CASCADE;


--
-- Name: fiscal_references fiscal_references_awarding_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_references
    ADD CONSTRAINT fiscal_references_awarding_org_id_fkey FOREIGN KEY (awarding_org_id) REFERENCES public.orgs(id);


--
-- Name: fiscal_references fiscal_references_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_references
    ADD CONSTRAINT fiscal_references_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id);


--
-- Name: fiscal_references fiscal_references_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_references
    ADD CONSTRAINT fiscal_references_program_id_fkey FOREIGN KEY (program_id) REFERENCES public.funding_programs(id);


--
-- Name: fiscal_references fiscal_references_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fiscal_references
    ADD CONSTRAINT fiscal_references_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: body_lineage fk_bl_matter; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_lineage
    ADD CONSTRAINT fk_bl_matter FOREIGN KEY (authorizing_matter_id) REFERENCES public.matters(id);


--
-- Name: footnote_references footnote_references_document_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnote_references
    ADD CONSTRAINT footnote_references_document_section_id_fkey FOREIGN KEY (document_section_id) REFERENCES public.document_sections(id);


--
-- Name: footnote_references footnote_references_footnote_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnote_references
    ADD CONSTRAINT footnote_references_footnote_id_fkey FOREIGN KEY (footnote_id) REFERENCES public.footnotes(id);


--
-- Name: footnotes footnotes_contact_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnotes
    ADD CONSTRAINT footnotes_contact_person_id_fkey FOREIGN KEY (contact_person_id) REFERENCES public.persons(id);


--
-- Name: footnotes footnotes_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnotes
    ADD CONSTRAINT footnotes_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);


--
-- Name: footnotes footnotes_document_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.footnotes
    ADD CONSTRAINT footnotes_document_section_id_fkey FOREIGN KEY (document_section_id) REFERENCES public.document_sections(id);


--
-- Name: framework_categories framework_categories_framework_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT framework_categories_framework_id_fkey FOREIGN KEY (framework_id) REFERENCES public.frameworks(id) ON DELETE CASCADE;


--
-- Name: framework_categories framework_categories_parent_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT framework_categories_parent_category_id_fkey FOREIGN KEY (parent_category_id) REFERENCES public.framework_categories(id);


--
-- Name: frameworks frameworks_defining_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frameworks
    ADD CONSTRAINT frameworks_defining_document_id_fkey FOREIGN KEY (defining_document_id) REFERENCES public.documents(id);


--
-- Name: frameworks frameworks_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frameworks
    ADD CONSTRAINT frameworks_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: funding_program_aliases funding_program_aliases_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_program_aliases
    ADD CONSTRAINT funding_program_aliases_program_id_fkey FOREIGN KEY (program_id) REFERENCES public.funding_programs(id);


--
-- Name: funding_programs funding_programs_administering_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_programs
    ADD CONSTRAINT funding_programs_administering_org_id_fkey FOREIGN KEY (administering_org_id) REFERENCES public.orgs(id);


--
-- Name: funding_programs funding_programs_parent_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funding_programs
    ADD CONSTRAINT funding_programs_parent_program_id_fkey FOREIGN KEY (parent_program_id) REFERENCES public.funding_programs(id);


--
-- Name: issue_aliases issue_aliases_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_aliases
    ADD CONSTRAINT issue_aliases_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.issues(id);


--
-- Name: issues issues_topic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.topics(id);


--
-- Name: jurisdiction_attributes jurisdiction_attributes_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_attributes
    ADD CONSTRAINT jurisdiction_attributes_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: jurisdiction_citations jurisdiction_citations_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_citations
    ADD CONSTRAINT jurisdiction_citations_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: jurisdiction_citations jurisdiction_citations_comparability_disputed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jurisdiction_citations
    ADD CONSTRAINT jurisdiction_citations_comparability_disputed_by_fkey FOREIGN KEY (comparability_disputed_by) REFERENCES public.persons(id);


--
-- Name: matter_versions matter_versions_matter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matter_versions
    ADD CONSTRAINT matter_versions_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES public.matters(id);


--
-- Name: matters matters_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matters
    ADD CONSTRAINT matters_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: matters matters_venue_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matters
    ADD CONSTRAINT matters_venue_jurisdiction_id_fkey FOREIGN KEY (venue_jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: media_assets media_assets_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: memberships memberships_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_body_id_fkey FOREIGN KEY (body_id) REFERENCES public.bodies(id);


--
-- Name: memberships memberships_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id);


--
-- Name: memberships memberships_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id);


--
-- Name: org_aliases org_aliases_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_aliases
    ADD CONSTRAINT org_aliases_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id) ON DELETE CASCADE;


--
-- Name: orgs orgs_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orgs
    ADD CONSTRAINT orgs_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: orgs orgs_merged_into_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orgs
    ADD CONSTRAINT orgs_merged_into_id_fkey FOREIGN KEY (merged_into_id) REFERENCES public.orgs(id);


--
-- Name: page_manifests page_manifests_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_manifests
    ADD CONSTRAINT page_manifests_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: page_manifests page_manifests_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_manifests
    ADD CONSTRAINT page_manifests_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_versions page_versions_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_versions
    ADD CONSTRAINT page_versions_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id);


--
-- Name: person_aliases person_aliases_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_aliases
    ADD CONSTRAINT person_aliases_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id);


--
-- Name: person_voiceprints person_voiceprints_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.person_voiceprints
    ADD CONSTRAINT person_voiceprints_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id) ON DELETE CASCADE;


--
-- Name: posts posts_body_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_body_id_fkey FOREIGN KEY (body_id) REFERENCES public.bodies(id);


--
-- Name: program_diffusion program_diffusion_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_diffusion
    ADD CONSTRAINT program_diffusion_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id);


--
-- Name: program_diffusion program_diffusion_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_diffusion
    ADD CONSTRAINT program_diffusion_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: quantities quantities_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quantities
    ADD CONSTRAINT quantities_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id) ON DELETE CASCADE;


--
-- Name: quantities quantities_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quantities
    ADD CONSTRAINT quantities_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: research_questions research_questions_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_questions
    ADD CONSTRAINT research_questions_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.issues(id);


--
-- Name: research_questions research_questions_origin_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_questions
    ADD CONSTRAINT research_questions_origin_claim_id_fkey FOREIGN KEY (origin_claim_id) REFERENCES public.claims(id);


--
-- Name: research_questions research_questions_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research_questions
    ADD CONSTRAINT research_questions_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: segments segments_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: segments segments_event_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_event_item_id_fkey FOREIGN KEY (event_item_id) REFERENCES public.event_items(id);


--
-- Name: segments segments_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: source_search_log source_search_log_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_search_log
    ADD CONSTRAINT source_search_log_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: source_targets source_targets_research_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_targets
    ADD CONSTRAINT source_targets_research_question_id_fkey FOREIGN KEY (research_question_id) REFERENCES public.research_questions(id);


--
-- Name: source_targets source_targets_resolved_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_targets
    ADD CONSTRAINT source_targets_resolved_document_id_fkey FOREIGN KEY (resolved_document_id) REFERENCES public.documents(id);


--
-- Name: subject_aliases subject_aliases_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_aliases
    ADD CONSTRAINT subject_aliases_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: subject_framework_categories subject_framework_categories_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_framework_categories
    ADD CONSTRAINT subject_framework_categories_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.framework_categories(id);


--
-- Name: subject_framework_categories subject_framework_categories_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_framework_categories
    ADD CONSTRAINT subject_framework_categories_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: subject_matters subject_matters_matter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_matters
    ADD CONSTRAINT subject_matters_matter_id_fkey FOREIGN KEY (matter_id) REFERENCES public.matters(id);


--
-- Name: subject_matters subject_matters_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_matters
    ADD CONSTRAINT subject_matters_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: subject_places subject_places_claim_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_places
    ADD CONSTRAINT subject_places_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES public.claims(id);


--
-- Name: subject_places subject_places_place_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_places
    ADD CONSTRAINT subject_places_place_subject_id_fkey FOREIGN KEY (place_subject_id) REFERENCES public.subjects(id);


--
-- Name: subject_places subject_places_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_places
    ADD CONSTRAINT subject_places_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subjects(id);


--
-- Name: subjects subjects_jurisdiction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_jurisdiction_id_fkey FOREIGN KEY (jurisdiction_id) REFERENCES public.jurisdictions(id);


--
-- Name: subjects subjects_merged_into_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_merged_into_id_fkey FOREIGN KEY (merged_into_id) REFERENCES public.subjects(id);


--
-- Name: subjects subjects_parent_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_parent_subject_id_fkey FOREIGN KEY (parent_subject_id) REFERENCES public.subjects(id);


--
-- Name: subjects subjects_topic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subjects
    ADD CONSTRAINT subjects_topic_id_fkey FOREIGN KEY (topic_id) REFERENCES public.topics(id);


--
-- Name: topics topics_parent_topic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topics
    ADD CONSTRAINT topics_parent_topic_id_fkey FOREIGN KEY (parent_topic_id) REFERENCES public.topics(id);


--
-- Name: utterances utterances_media_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_media_asset_id_fkey FOREIGN KEY (media_asset_id) REFERENCES public.media_assets(id);


--
-- Name: utterances utterances_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.orgs(id);


--
-- Name: utterances utterances_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id);


--
-- Name: utterances utterances_responds_to_utterance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_responds_to_utterance_id_fkey FOREIGN KEY (responds_to_utterance_id) REFERENCES public.utterances(id);


--
-- Name: utterances utterances_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.utterances
    ADD CONSTRAINT utterances_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id);


--
-- Name: vocabulary_proposals vocabulary_proposals_vocabulary_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabulary_proposals
    ADD CONSTRAINT vocabulary_proposals_vocabulary_fkey FOREIGN KEY (vocabulary) REFERENCES public.vocabularies(name);


--
-- Name: vocabulary_terms vocabulary_terms_vocabulary_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vocabulary_terms
    ADD CONSTRAINT vocabulary_terms_vocabulary_fkey FOREIGN KEY (vocabulary) REFERENCES public.vocabularies(name) ON DELETE CASCADE;


--
-- Name: votes votes_event_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_event_item_id_fkey FOREIGN KEY (event_item_id) REFERENCES public.event_items(id);


--
-- Name: votes votes_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.persons(id);


--
-- PostgreSQL database dump complete
--

\unrestrict ruf1Kbwt2NhutCnr3gj1mSGIkR6G3aJSHlU89UboWihxaZs0iCQOOZGhQc3Q1ki

