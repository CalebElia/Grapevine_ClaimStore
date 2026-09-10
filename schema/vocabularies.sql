-- ============================================================================
-- GRAPEVINE CLAIM STORE — VOCABULARY SEED + TRIGGER ATTACHMENT
-- v0.2 — 2026-07-28
--
-- Apply AFTER claim_store.sql.
--
-- These are the APPROVED terms as of schema v0.2. They are a starting point, not a
-- closed world. Rule 1: the model picks the closest approved term and proposes when
-- nothing fits; the trigger in claim_store.sql logs the proposal and never rejects
-- the insert; a human approves growth by adding a row here (or via the review queue).
--
-- approved_by = 'schema-v0.2' means "came from a human-reviewed design document",
-- which is different from a model proposing a term at runtime. Nothing in this file
-- was auto-generated.
--
-- fallback_term is the BACKSTOP the trigger substitutes for an unapproved value.
-- It must itself appear in vocabulary_terms. NULL means "store the unknown value
-- as-is and flag it" — right for high-cardinality descriptive fields where
-- substituting would destroy information.
-- ============================================================================

-- ---------------------------------------------------------------- vocabularies
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
-- Layer 1 — structural
('government_form',        'Municipal governance form, time-varying via jurisdiction_attributes', TRUE,  'unknown'),
('utility_governance',     'Who owns/regulates the electric or gas utility',                       TRUE,  'unknown'),
('body_classification',    'Kind of governing body',                                               TRUE,  'other'),
('authority_type',         'Whether a body advises, binds, or adjudicates',                        TRUE,  'unknown'),
('body_lineage_relation',  'How a successor body relates to a predecessor',                        TRUE,  'succeeded_by'),
('post_role',              'Role attached to a seat',                                              TRUE,  'member'),
('alias_type',             'Why an alias exists',                                                  TRUE,  'other'),
('org_type',               'Kind of organization',                                                 TRUE,  'other'),
('affiliation_source',     'Evidence backing a person-org affiliation',                            TRUE,  'other'),
('venue_type',             'Where authority over a matter sits. GROWS as corpora are added.',      TRUE,  'municipal_legislative'),
('meeting_kind',           'Kind of meeting',                                                      TRUE,  'other'),
('vote_value',             'How one member voted',                                                 FALSE, NULL),
('media_host',             'Where meeting video is hosted',                                        TRUE,  'other'),
-- Legistar's meeting->video association is evidence, not ground truth: one video was
-- attached to the wrong meeting row, seven weeks off. This records the check.
('video_date_verification','Whether a host video actually belongs to the meeting it is linked from', TRUE, 'unverified'),
-- How we came to believe a video is a given meeting. A city-published link is an
-- assertion; a title match is our inference. Different evidence, kept apart.
('media_discovery_method','How a media asset was associated with its meeting',      TRUE,  'legistar_calendar'),
('doc_type',               'Source document type. EXPECTED TO GROW — Rule 2.',                     TRUE,  NULL),
-- Layer 2 — spine
('objective_outcome',      'Whether a case achieved its formal objective. NOT binary.',            FALSE, 'unknown'),
('collateral_gains',       'Gains extracted even where the formal objective failed',               FALSE, 'unknown'),
-- Layer 3 — transcript
('segment_kind',           'What a meeting section is',                                            TRUE,  'procedural'),
('deviation_kind',         'How live proceedings deviated from the published agenda',              TRUE,  'other'),
('actor_capacity',         'The capacity a person speaks in at this moment',                       TRUE,  'unknown'),
('speaker_id_method',      'How a speaker was identified. Also used for voiceprint enrollment.',   TRUE,  'manual'),
-- Layer 4 — claims
('curation_state',         'How far a claim has progressed through Subject assignment',            FALSE, 'unassigned'),
-- How a claim-to-subject MENTION was found. Open, and with no fallback on purpose: an
-- unrecognised method must stay visible rather than be silently rewritten to a weaker one.
('mention_method',         'How a mention of a subject inside a claim was detected',              TRUE,  NULL),
-- Folded in from migrations 005-022. Declared here, not in the folded section below,
-- because tests/test_schema_structure.py reads only the first VALUES block.
('parse_confidence',      'How far a section''s parse has been audited',                          FALSE, 'unaudited'),
('section_topic',         'What a section IS, when that is not derivable from its heading',       TRUE,  NULL),
('human_verdict',         'A person''s sign-off on a parsed section',                             TRUE,  'not_reviewed'),
('covers_period_source',  'How a document''s reporting period was determined',                    FALSE, 'unknown'),
('provenance_source',     'Where a stored artefact came from',                                    TRUE,  'unknown'),
('marker_evidence',       'What evidence placed a page marker',                                   TRUE,  'unknown'),
('fiscal_direction',      'Which way money moved: authorized, spent, received, saved',            TRUE,  'unknown'),
('model_confidence',      'A model''s confidence reading a value off a figure',                   TRUE,  'unknown'),
('subject_kind',          'What sort of thing a subject is: plan, strategy, initiative, place', TRUE,  'other'),
('polarity',               'Direction of a claim',                                                 FALSE, 'informational'),
('contested_dimension',    'What is actually in dispute. "Not now" is not "no".',                  TRUE,  NULL),
('modality',               'Epistemic stance of the assertion',                                    FALSE, 'asserted'),
('speech_act',             'Utterance type. ORAL SOURCES ONLY — null for documents.',              TRUE,  NULL),
('source_type',            'Corpus the claim came from',                                           TRUE,  NULL),
('span_unit',              'Unit of span_start/span_end. Never mix.',                              FALSE, NULL),
('date_precision',         'Granularity of an asserted date',                                      TRUE,  'unresolved'),
('calendar_system',        'Calendar vs fiscal year reckoning',                                    FALSE, 'calendar'),
('reason_class',           'Kind of reason offered. Also argument_class on arguments.',            TRUE,  'other'),
('condition_origin',       'Whether a condition is a negotiating move or a documented constraint', TRUE,  'documented_constraint'),
('is_met',                 'Whether a condition has been satisfied',                               FALSE, 'unknown'),
('claim_relation',         'Dialectical relation between two claims',                              TRUE,  NULL),
('citation_stance',        'How a speaker characterized another jurisdiction',                     TRUE,  'precedent'),
-- Layer 5 — referent
('asserted_event_class',   'Kind of real-world event being attested',                              TRUE,  'other'),
('attestation_type',       'INDEPENDENCE of a source, not merely its presence',                    TRUE,  'restatement'),
-- Layer 6 — typed findings
('decision_type',          'Kind of formal decision',                                              TRUE,  'other'),
('decision_outcome',       'Result of a decision',                                                 TRUE,  'unknown'),
('alternative_disposition','What happened to a considered alternative',                            FALSE, 'untested'),
('rejection_class',        'Why an alternative was rejected. Feeds scope conditions.',             TRUE,  'other'),
('commitment_status',      'State of an open loop',                                                TRUE,  'open'),
('funding_source',         'Where money came from',                                                TRUE,  'other'),
-- NB: no '- -' (joined) inside a description string. The repo's SQL guards strip line
-- comments without tracking string literals, so a double dash here truncates the row
-- and test_parens_balanced fails pointing at the whole file. Use a colon or a comma.
('funding_instrument',     'The financial mechanism of an award: not its source, not its program', TRUE,  'other'),
('involvement_role',       'How an actor is involved in an initiative',                             TRUE,  'participant'),
('funding_recurrence',     'One-time vs recurring',                                                TRUE,  'unknown'),
('award_status',           'Lifecycle of an award. REVERSALS ARE THE FINDING.',                    TRUE,  'unknown'),
('quantity_unit',          'Unit of a non-monetary quantity',                                      TRUE,  NULL),
('quantity_measure',       'What is being measured',                                               TRUE,  'other'),
('quantity_bound',         'Whether a value is exact or bounded',                                  FALSE, 'exact'),
('barrier_class',          'Kind of obstacle. Recurs across cases — the cross-case signal.',        TRUE,  'other'),
('coalition_type',         'Kind of coalition',                                                    TRUE,  'informal'),
('diffusion_relation',     'How a program spread to another jurisdiction',                         TRUE,  'replicated_by'),
-- Layer 7/8 — render, curation, growth
('page_type',              'Rendered page type',                                                   TRUE,  NULL),
('curation_decision',      'A durable curation decision, including negative ones',                 FALSE, NULL),
('review_reason',          'Why something is in the review queue. Measure minutes BY THIS.',       TRUE,  'other'),
('research_question_origin','What generated a research question',                                  TRUE,  'human'),
('source_target_status',   'Acquisition state of a wanted source',                                 TRUE,  'identified'),
('search_outcome',         'Result of looking for a source. Distinguishes absent from unsearched.',FALSE, NULL);

-- ------------------------------------------------------------ vocabulary_terms
INSERT INTO vocabulary_terms (vocabulary, term, approved_by) VALUES
-- Layer 1
('government_form','council_manager','schema-v0.2'),('government_form','strong_mayor','schema-v0.2'),
('government_form','weak_mayor','schema-v0.2'),('government_form','commission','schema-v0.2'),
('government_form','town_meeting','schema-v0.2'),('government_form','unknown','schema-v0.2'),

('utility_governance','municipal','schema-v0.2'),('utility_governance','iou_under_puc','schema-v0.2'),
('utility_governance','coop','schema-v0.2'),('utility_governance','mixed','schema-v0.2'),
('utility_governance','unknown','schema-v0.2'),

('body_classification','legislature','schema-v0.2'),('body_classification','commission','schema-v0.2'),
('body_classification','committee','schema-v0.2'),('body_classification','authority','schema-v0.2'),
('body_classification','board','schema-v0.2'),('body_classification','other','schema-v0.2'),

('authority_type','advisory','schema-v0.2'),('authority_type','binding','schema-v0.2'),
('authority_type','quasi_judicial','schema-v0.2'),('authority_type','unknown','schema-v0.2'),

-- Ann Arbor 2025: Energy + Environmental Commissions merged into Sustainability.
('body_lineage_relation','merged_into','schema-v0.2'),('body_lineage_relation','renamed_to','schema-v0.2'),
('body_lineage_relation','split_from','schema-v0.2'),('body_lineage_relation','succeeded_by','schema-v0.2'),

('post_role','member','schema-v0.2'),('post_role','chair','schema-v0.2'),
('post_role','vice_chair','schema-v0.2'),('post_role','mayor','schema-v0.2'),
('post_role','staff_liaison','schema-v0.2'),('post_role','youth_member','schema-v0.2'),

('alias_type','asr_variant','schema-v0.2'),('alias_type','nickname','schema-v0.2'),
('alias_type','formal','schema-v0.2'),('alias_type','misspelling','schema-v0.2'),
('alias_type','colloquial','schema-v0.2'),('alias_type','file_number','schema-v0.2'),
('alias_type','acronym','schema-v0.2'),('alias_type','other','schema-v0.2'),

('org_type','government','schema-v0.2'),('org_type','government_dept','schema-v0.2'),
('org_type','nonprofit','schema-v0.2'),('org_type','neighborhood_group','schema-v0.2'),
('org_type','business','schema-v0.2'),('org_type','utility','schema-v0.2'),
-- faith_group: the CAP names Places of Worship as their own engagement channel in
-- Strategy 3, so a faith organisation is a distinct route to residents. 'nonprofit'
-- would be technically true and lose exactly that.
('org_type','faith_group','schema-v0.5'),
('quantity_measure','generation_mix','caleb'),
('parse_confidence','clean','schema-v0.5'),('parse_confidence','known_incomplete','schema-v0.5'),('parse_confidence','suspect','schema-v0.5'),('parse_confidence','unaudited','schema-v0.5'),
('section_topic','assumptions','schema-v0.5'),('section_topic','engagement_log','schema-v0.5'),('section_topic','ideas_considered','schema-v0.5'),('section_topic','roster','schema-v0.5'),('section_topic','timeline','schema-v0.5'),
('human_verdict','approved','schema-v0.5'),('human_verdict','approved_with_caveats','schema-v0.5'),('human_verdict','not_reviewed','schema-v0.5'),('human_verdict','rejected','schema-v0.5'),
('covers_period_source','human_estimate','schema-v0.5'),('covers_period_source','stated','schema-v0.5'),('covers_period_source','unknown','schema-v0.5'),
('provenance_source','human_supplied','schema-v0.5'),('provenance_source','local_file','schema-v0.5'),('provenance_source','retrieved','schema-v0.5'),('provenance_source','unknown','schema-v0.5'),
('marker_evidence','human','schema-v0.5'),('marker_evidence','printed','schema-v0.5'),('marker_evidence','second_read','schema-v0.5'),('marker_evidence','text_layer','schema-v0.5'),('marker_evidence','unknown','schema-v0.5'),
('fiscal_direction','authorized','schema-v0.5'),('fiscal_direction','disbursed','schema-v0.5'),('fiscal_direction','received','schema-v0.5'),('fiscal_direction','saved','schema-v0.5'),('fiscal_direction','spent','schema-v0.5'),('fiscal_direction','unknown','schema-v0.5'),
('model_confidence','high','schema-v0.5'),('model_confidence','medium','schema-v0.5'),('model_confidence','low','schema-v0.5'),('model_confidence','unknown','schema-v0.5'),
('org_type','union','schema-v0.2'),('org_type','consultant','schema-v0.2'),
('org_type','academic','schema-v0.2'),('org_type','foundation','schema-v0.2'),
('org_type','advocacy_coalition','schema-v0.2'),('org_type','other','schema-v0.2'),

('affiliation_source','self_id','schema-v0.2'),('affiliation_source','lobbyist_registry','schema-v0.2'),
('affiliation_source','irs_990','schema-v0.2'),('affiliation_source','minutes','schema-v0.2'),
('affiliation_source','website','schema-v0.2'),('affiliation_source','roster','schema-v0.2'),
('affiliation_source','other','schema-v0.2'),

-- venue_type: the MPSC finding lives here. Ann Arbor claims ~$1B in ratepayer
-- savings won at the Michigan Public Service Commission, which is not Legistar.
('venue_type','municipal_legislative','schema-v0.2'),('venue_type','ballot_measure','schema-v0.2'),
('venue_type','state_legislation','schema-v0.2'),('venue_type','regulatory_docket','schema-v0.2'),
('venue_type','federal_grant_program','schema-v0.2'),('venue_type','court','schema-v0.2'),
('venue_type','administrative','schema-v0.2'),

('meeting_kind','regular','schema-v0.2'),('meeting_kind','work_session','schema-v0.2'),
('meeting_kind','planning_session','schema-v0.2'),('meeting_kind','special','schema-v0.2'),
('meeting_kind','cancelled','schema-v0.2'),('meeting_kind','other','schema-v0.2'),

('vote_value','yea','schema-v0.2'),('vote_value','nay','schema-v0.2'),
('vote_value','abstain','schema-v0.2'),('vote_value','absent','schema-v0.2'),
('vote_value','recuse','schema-v0.2'),

('video_date_verification','matched','schema-v0.2'),
('video_date_verification','title_mismatch','schema-v0.2'),
('video_date_verification','upload_implausible','schema-v0.2'),
('video_date_verification','no_title_date','schema-v0.2'),
('video_date_verification','unverified','schema-v0.2'),
('media_discovery_method','legistar_calendar','schema-v0.2'),
('media_discovery_method','host_channel','schema-v0.2'),
('media_discovery_method','both_corroborated','schema-v0.2'),
('media_discovery_method','manual','schema-v0.2'),
('media_host','youtube','schema-v0.2'),('media_host','granicus','schema-v0.2'),
('media_host','vimeo','schema-v0.2'),('media_host','direct','schema-v0.2'),
('media_host','other','schema-v0.2'),

-- doc_type — Rule 2 growth order. Wave 1 is in scope now.
('doc_type','plan','schema-v0.2'),('doc_type','annual_report','schema-v0.2'),
('doc_type','staff_report','schema-v0.2'),('doc_type','minutes','schema-v0.2'),
('doc_type','agenda','schema-v0.2'),('doc_type','memo','schema-v0.2'),
('doc_type','webpage','schema-v0.2'),('doc_type','dashboard','schema-v0.2'),
-- Wave 2: promoted ahead of news on the strength of the MPSC finding.
('doc_type','regulatory_filing','schema-v0.2'),('doc_type','regulatory_testimony','schema-v0.2'),
('doc_type','regulatory_order','schema-v0.2'),
('doc_type','ballot_material','schema-v0.2'),('doc_type','legislation','schema-v0.2'),
('doc_type','rfp','schema-v0.2'),('doc_type','contract','schema-v0.2'),
-- Wave 3
('doc_type','correspondence','schema-v0.2'),('doc_type','ecomment','schema-v0.2'),
('doc_type','third_party_case_study','schema-v0.2'),('doc_type','press_release','schema-v0.2'),
-- Deferred but hooked
('doc_type','news','schema-v0.2'),

-- Layer 2
('objective_outcome','yes','schema-v0.2'),('objective_outcome','no','schema-v0.2'),
('objective_outcome','partial','schema-v0.2'),('objective_outcome','in_progress','schema-v0.2'),
('objective_outcome','unknown','schema-v0.2'),
('collateral_gains','none','schema-v0.2'),('collateral_gains','minor','schema-v0.2'),
('collateral_gains','substantial','schema-v0.2'),('collateral_gains','unknown','schema-v0.2'),

-- Layer 3
('segment_kind','roll_call','schema-v0.2'),('segment_kind','agenda_approval','schema-v0.2'),
('segment_kind','minutes_approval','schema-v0.2'),('segment_kind','public_comment','schema-v0.2'),
('segment_kind','staff_presentation','schema-v0.2'),('segment_kind','deliberation','schema-v0.2'),
('segment_kind','motion_vote','schema-v0.2'),('segment_kind','work_group_update','schema-v0.2'),
('segment_kind','staff_update','schema-v0.2'),('segment_kind','procedural','schema-v0.2'),
('segment_kind','adjournment','schema-v0.2'),('segment_kind','presentation','schema-v0.2'),

-- Observed on the one segmented meeting: two agendized items discussed together,
-- and an item the chair skipped that staff pulled back before adjournment.
('deviation_kind','items_merged','schema-v0.2'),('deviation_kind','item_skipped','schema-v0.2'),
('deviation_kind','item_added','schema-v0.2'),('deviation_kind','order_changed','schema-v0.2'),
('deviation_kind','item_postponed','schema-v0.2'),('deviation_kind','item_recovered','schema-v0.2'),
('deviation_kind','other','schema-v0.2'),

('actor_capacity','official','schema-v0.2'),('actor_capacity','staff','schema-v0.2'),
('actor_capacity','public','schema-v0.2'),('actor_capacity','organizational_rep','schema-v0.2'),
('actor_capacity','expert','schema-v0.2'),('actor_capacity','utility_rep','schema-v0.2'),
('actor_capacity','unknown','schema-v0.2'),

('speaker_id_method','self_id','schema-v0.2'),('speaker_id_method','chair_address','schema-v0.2'),
('speaker_id_method','roll_call','schema-v0.2'),('speaker_id_method','voiceprint','schema-v0.2'),
('speaker_id_method','roster','schema-v0.2'),('speaker_id_method','ocr','schema-v0.2'),
('speaker_id_method','manual','schema-v0.2'),

-- Layer 4
('curation_state','unassigned','schema-v0.2'),('curation_state','proposed','schema-v0.2'),
('curation_state','confirmed','schema-v0.2'),
-- Only a LITERAL occurrence is strong enough to store; proximity is queued, never written.
('mention_method','literal_name','schema-v0.4'),('mention_method','literal_alias','schema-v0.4'),
('mention_method','human','schema-v0.4'),
('mention_method','core_phrase_verified','schema-v0.4'),
('subject_kind','plan','schema-v0.3'),('subject_kind','strategy','schema-v0.3'),
('subject_kind','initiative','schema-v0.3'),('subject_kind','place','schema-v0.3'),
('subject_kind','topic','schema-v0.3'),('subject_kind','other','schema-v0.3'),

('polarity','support','schema-v0.2'),('polarity','oppose','schema-v0.2'),
('polarity','mixed','schema-v0.2'),('polarity','procedural','schema-v0.2'),
('polarity','informational','schema-v0.2'),

('contested_dimension','substance','schema-v0.2'),('contested_dimension','timing','schema-v0.2'),
('contested_dimension','cost','schema-v0.2'),('contested_dimension','process','schema-v0.2'),
('contested_dimension','vehicle','schema-v0.2'),('contested_dimension','scope','schema-v0.2'),
('contested_dimension','evidence_sufficiency','schema-v0.2'),

('modality','asserted','schema-v0.2'),('modality','hedged','schema-v0.2'),
('modality','hypothetical','schema-v0.2'),('modality','attributed_to_other','schema-v0.2'),

('speech_act','prepared_statement','schema-v0.2'),('speech_act','live_deliberation','schema-v0.2'),
('speech_act','question','schema-v0.2'),('speech_act','response','schema-v0.2'),
('speech_act','public_comment','schema-v0.2'),('speech_act','staff_presentation','schema-v0.2'),
('speech_act','procedural','schema-v0.2'),

('source_type','video_utterance','schema-v0.2'),('source_type','plan','schema-v0.2'),
('source_type','annual_report','schema-v0.2'),('source_type','staff_report','schema-v0.2'),
('source_type','minutes','schema-v0.2'),('source_type','memo','schema-v0.2'),
('source_type','correspondence','schema-v0.2'),('source_type','ecomment','schema-v0.2'),
('source_type','webpage','schema-v0.2'),('source_type','regulatory_filing','schema-v0.2'),
('source_type','third_party_case_study','schema-v0.2'),('source_type','news','schema-v0.2'),

('span_unit','ms','schema-v0.2'),('span_unit','char','schema-v0.2'),

-- 'era' covers "late 1960s and early 1970s"; 'relative' covers "next budget cycle".
('date_precision','day','schema-v0.2'),('date_precision','month','schema-v0.2'),
('date_precision','quarter','schema-v0.2'),('date_precision','year','schema-v0.2'),
('date_precision','fiscal_year','schema-v0.2'),('date_precision','era','schema-v0.2'),
('date_precision','relative','schema-v0.2'),('date_precision','unresolved','schema-v0.2'),

('calendar_system','calendar','schema-v0.2'),('calendar_system','fiscal','schema-v0.2'),

('reason_class','performance','schema-v0.2'),('reason_class','agency','schema-v0.2'),
('reason_class','source_credibility','schema-v0.2'),('reason_class','feasibility_precedent','schema-v0.2'),
('reason_class','epistemic','schema-v0.2'),('reason_class','legal','schema-v0.2'),
('reason_class','fiscal','schema-v0.2'),('reason_class','equity','schema-v0.2'),
('reason_class','procedural','schema-v0.2'),('reason_class','technical','schema-v0.2'),
('reason_class','other','schema-v0.2'),

-- CAP-2020 stated a legislative prerequisite for Community Choice Aggregation in a
-- 2020 planning document. That is a condition, but not a negotiating move.
('condition_origin','negotiating_move','schema-v0.2'),
('condition_origin','documented_constraint','schema-v0.2'),
('condition_origin','design_assumption','schema-v0.2'),
('condition_origin','regulatory_prerequisite','schema-v0.2'),

('is_met','true','schema-v0.2'),('is_met','false','schema-v0.2'),('is_met','unknown','schema-v0.2'),

('claim_relation','supports','schema-v0.2'),('claim_relation','contradicts','schema-v0.2'),
('claim_relation','elaborates','schema-v0.2'),('claim_relation','supersedes','schema-v0.2'),
('claim_relation','concedes_to','schema-v0.2'),('claim_relation','responds_to','schema-v0.2'),
('claim_relation','trades_off_against','schema-v0.2'),

-- Boulder was cited as a failure that produced substantial gains. Binary erases that.
('citation_stance','success','schema-v0.2'),('citation_stance','failure','schema-v0.2'),
('citation_stance','failure_with_gains','schema-v0.2'),('citation_stance','cautionary','schema-v0.2'),
('citation_stance','precedent','schema-v0.2'),('citation_stance','counterexample','schema-v0.2'),

-- Layer 5
('asserted_event_class','decision','schema-v0.2'),('asserted_event_class','vote','schema-v0.2'),
('asserted_event_class','funding_award','schema-v0.2'),('asserted_event_class','funding_reversal','schema-v0.2'),
('asserted_event_class','milestone','schema-v0.2'),('asserted_event_class','commitment','schema-v0.2'),
('asserted_event_class','publication','schema-v0.2'),('asserted_event_class','appointment','schema-v0.2'),
('asserted_event_class','contract','schema-v0.2'),('asserted_event_class','launch','schema-v0.2'),
('asserted_event_class','failure','schema-v0.2'),('asserted_event_class','other','schema-v0.2'),

-- THE INDEPENDENCE LADDER. A third-party case study whose contributor is city staff
-- is 'secondhand' with a derives_from pointer, NOT an independent second source.
-- Only the first three count toward corroboration.
('attestation_type','primary_record','schema-v0.2'),('attestation_type','firsthand','schema-v0.2'),
('attestation_type','secondhand','schema-v0.2'),('attestation_type','restatement','schema-v0.2'),
('attestation_type','boilerplate','schema-v0.2'),

-- Layer 6
('decision_type','motion','schema-v0.2'),('decision_type','amendment','schema-v0.2'),
('decision_type','substitute','schema-v0.2'),('decision_type','continuance','schema-v0.2'),
('decision_type','postponement','schema-v0.2'),('decision_type','referral','schema-v0.2'),
('decision_type','first_reading','schema-v0.2'),('decision_type','final_vote','schema-v0.2'),
('decision_type','other','schema-v0.2'),

('decision_outcome','passed','schema-v0.2'),('decision_outcome','failed','schema-v0.2'),
('decision_outcome','withdrawn','schema-v0.2'),('decision_outcome','tabled','schema-v0.2'),
('decision_outcome','unknown','schema-v0.2'),

('alternative_disposition','adopted','schema-v0.2'),('alternative_disposition','rejected','schema-v0.2'),
('alternative_disposition','deferred','schema-v0.2'),('alternative_disposition','untested','schema-v0.2'),

-- Observed: air-source heat pumps rejected on operating cost; utility-owned
-- geothermal rejected because Michigan lacks enabling legislation; full
-- municipalization rejected on cost and timeline.
('rejection_class','legal_authority','schema-v0.2'),('rejection_class','cost','schema-v0.2'),
('rejection_class','operating_cost','schema-v0.2'),('rejection_class','timeline','schema-v0.2'),
('rejection_class','technical','schema-v0.2'),('rejection_class','political','schema-v0.2'),
('rejection_class','equity','schema-v0.2'),('rejection_class','capacity','schema-v0.2'),
('rejection_class','other','schema-v0.2'),

('commitment_status','open','schema-v0.2'),('commitment_status','fulfilled','schema-v0.2'),
('commitment_status','lapsed','schema-v0.2'),('commitment_status','superseded','schema-v0.2'),
('commitment_status','abandoned','schema-v0.2'),

('funding_source','millage','schema-v0.2'),('funding_source','federal_grant','schema-v0.2'),
('funding_instrument','block_grant','schema-v0.2'),('funding_instrument','competitive_grant','schema-v0.2'),
('involvement_role','lead','schema-v0.3'),('involvement_role','co_lead','schema-v0.3'),
('involvement_role','implementer','schema-v0.3'),('involvement_role','community_partner','schema-v0.3'),
('involvement_role','funder','schema-v0.3'),('involvement_role','regulator','schema-v0.3'),
('involvement_role','beneficiary','schema-v0.3'),('involvement_role','participant','schema-v0.3'),
('funding_instrument','formula_grant','schema-v0.2'),('funding_instrument','mini_grant','schema-v0.2'),
('funding_instrument','sponsorship','schema-v0.2'),('funding_instrument','rebate','schema-v0.2'),
('funding_instrument','loan','schema-v0.2'),('funding_instrument','revolving_loan','schema-v0.2'),
('funding_instrument','appropriation','schema-v0.2'),('funding_instrument','millage_revenue','schema-v0.2'),
('funding_instrument','other','schema-v0.2'),
('funding_source','state_grant','schema-v0.2'),('funding_source','county','schema-v0.2'),
('funding_source','general_fund','schema-v0.2'),('funding_source','bond','schema-v0.2'),
('funding_source','surplus','schema-v0.2'),('funding_source','rate_payer','schema-v0.2'),
('funding_source','local_match','schema-v0.2'),('funding_source','philanthropic','schema-v0.2'),
('funding_source','utility','schema-v0.2'),('funding_source','other','schema-v0.2'),

('funding_recurrence','one_time','schema-v0.2'),('funding_recurrence','annual','schema-v0.2'),
('funding_recurrence','multi_year','schema-v0.2'),('funding_recurrence','unknown','schema-v0.2'),

-- One annual report contained four reversals. A playbook citing a clawed-back
-- grant is the spurious-transferability failure mode in its purest form.
('award_status','applied','schema-v0.2'),('award_status','awarded','schema-v0.2'),
('award_status','obligated','schema-v0.2'),('award_status','disbursed','schema-v0.2'),
('award_status','on_hold','schema-v0.2'),('award_status','terminated','schema-v0.2'),
('award_status','rescinded','schema-v0.2'),('award_status','disputed','schema-v0.2'),
('award_status','withdrawn','schema-v0.2'),('award_status','lapsed','schema-v0.2'),
('award_status','unknown','schema-v0.2'),

('quantity_unit','MW','schema-v0.2'),('quantity_unit','kW','schema-v0.2'),
('quantity_unit','MWh','schema-v0.2'),('quantity_unit','tCO2e','schema-v0.2'),
('quantity_unit','USD','schema-v0.2'),('quantity_unit','count','schema-v0.2'),
('quantity_unit','pct','schema-v0.2'),('quantity_unit','acres','schema-v0.2'),
('quantity_unit','tons','schema-v0.2'),('quantity_unit','pounds','schema-v0.2'),
('quantity_unit','hours','schema-v0.2'),('quantity_unit','months','schema-v0.2'),
('quantity_unit','households','schema-v0.2'),('quantity_unit','people','schema-v0.2'),

('quantity_measure','installed_capacity','schema-v0.2'),('quantity_measure','emissions','schema-v0.2'),
('quantity_measure','goal_target','schema-v0.2'),('quantity_measure','participants','schema-v0.2'),
('quantity_measure','cost','schema-v0.2'),('quantity_measure','savings','schema-v0.2'),
('quantity_measure','vote_share','schema-v0.2'),('quantity_measure','diversion','schema-v0.2'),
('quantity_measure','adoption_rate','schema-v0.2'),('quantity_measure','duration','schema-v0.2'),
('quantity_measure','population','schema-v0.2'),('quantity_measure','other','schema-v0.2'),

('quantity_bound','exact','schema-v0.2'),('quantity_bound','at_least','schema-v0.2'),
('quantity_bound','at_most','schema-v0.2'),('quantity_bound','approximate','schema-v0.2'),

('barrier_class','legal','schema-v0.2'),('barrier_class','regulatory','schema-v0.2'),
('barrier_class','financial','schema-v0.2'),('barrier_class','technical','schema-v0.2'),
('barrier_class','political','schema-v0.2'),('barrier_class','administrative_capacity','schema-v0.2'),
('barrier_class','data_availability','schema-v0.2'),('barrier_class','workforce','schema-v0.2'),
('barrier_class','community_opposition','schema-v0.2'),('barrier_class','other','schema-v0.2'),

('coalition_type','work_group','schema-v0.2'),('coalition_type','advocacy_coalition','schema-v0.2'),
('coalition_type','interagency','schema-v0.2'),('coalition_type','joint_commission','schema-v0.2'),
('coalition_type','informal','schema-v0.2'),

('diffusion_relation','expanded_to','schema-v0.2'),('diffusion_relation','replicated_by','schema-v0.2'),
('diffusion_relation','partnership','schema-v0.2'),('diffusion_relation','exchange','schema-v0.2'),

-- Layer 7/8
('page_type','subject','schema-v0.2'),('page_type','issue','schema-v0.2'),
('page_type','person','schema-v0.2'),('page_type','org','schema-v0.2'),
('page_type','meeting','schema-v0.2'),('page_type','case','schema-v0.2'),
('page_type','divergence','schema-v0.2'),('page_type','argument','schema-v0.2'),
('page_type','timeline','schema-v0.2'),

('curation_decision','merge','schema-v0.2'),('curation_decision','keep_separate','schema-v0.2'),
('curation_decision','rename','schema-v0.2'),('curation_decision','reject','schema-v0.2'),

('review_reason','low_confidence','schema-v0.2'),('review_reason','has_conditions','schema-v0.2'),
('review_reason','decision_node','schema-v0.2'),('review_reason','load_bearing','schema-v0.2'),
('review_reason','extraction_disagreement','schema-v0.2'),('review_reason','entity_merge','schema-v0.2'),
('review_reason','date_anomaly','schema-v0.2'),('review_reason','empty_verbatim','schema-v0.2'),
-- Measured separately because they are different cost curves.
('review_reason','argument_naming','schema-v0.2'),('review_reason','event_merge','schema-v0.2'),
('review_reason','vocab_proposal','schema-v0.2'),('review_reason','coverage_gap','schema-v0.2'),
('review_reason','speaker_unresolved','schema-v0.2'),('review_reason','other','schema-v0.2'),

('research_question_origin','dark_matter_gap','schema-v0.2'),
('research_question_origin','outcome_without_mechanism','schema-v0.2'),
('research_question_origin','unmet_condition','schema-v0.2'),
('research_question_origin','commitment_unclosed','schema-v0.2'),
('research_question_origin','numeric_conflict','schema-v0.2'),
('research_question_origin','rejected_alternative','schema-v0.2'),
('research_question_origin','human','schema-v0.2'),

('source_target_status','identified','schema-v0.2'),('source_target_status','searched','schema-v0.2'),
('source_target_status','acquired','schema-v0.2'),('source_target_status','prepared','schema-v0.2'),
('source_target_status','ingested','schema-v0.2'),('source_target_status','not_found','schema-v0.2'),
('source_target_status','inaccessible','schema-v0.2'),('source_target_status','out_of_scope','schema-v0.2'),

-- 'none_exist' is the one that makes dark matter honest.
('search_outcome','found','schema-v0.2'),('search_outcome','none_exist','schema-v0.2'),
('search_outcome','exists_inaccessible','schema-v0.2'),('search_outcome','partial','schema-v0.2');

-- ============================================================================
-- TRIGGER ATTACHMENT
-- Args: (vocabulary_name, column_name [, pk_column]). pk defaults to 'id'.
-- ============================================================================

CREATE TRIGGER trg_vocab_jurisdictions_gov BEFORE INSERT OR UPDATE ON jurisdictions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('government_form','government_form');
CREATE TRIGGER trg_vocab_jurisdictions_util BEFORE INSERT OR UPDATE ON jurisdictions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('utility_governance','utility_governance');
CREATE TRIGGER trg_vocab_bodies_class BEFORE INSERT OR UPDATE ON bodies
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('body_classification','classification');
CREATE TRIGGER trg_vocab_bodies_auth BEFORE INSERT OR UPDATE ON bodies
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('authority_type','authority_type');
CREATE TRIGGER trg_vocab_lineage BEFORE INSERT OR UPDATE ON body_lineage
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('body_lineage_relation','relation');
CREATE TRIGGER trg_vocab_posts BEFORE INSERT OR UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('post_role','role');
CREATE TRIGGER trg_vocab_person_aliases BEFORE INSERT OR UPDATE ON person_aliases
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alias_type','alias_type');
CREATE TRIGGER trg_vocab_voiceprints BEFORE INSERT OR UPDATE ON person_voiceprints
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('speaker_id_method','enrolled_from');
CREATE TRIGGER trg_vocab_orgs BEFORE INSERT OR UPDATE ON orgs
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('org_type','org_type');
CREATE TRIGGER trg_vocab_affiliations BEFORE INSERT OR UPDATE ON affiliations
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('affiliation_source','source');
CREATE TRIGGER trg_vocab_matters BEFORE INSERT OR UPDATE ON matters
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('venue_type','venue_type');
CREATE TRIGGER trg_vocab_events BEFORE INSERT OR UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('meeting_kind','meeting_kind');
CREATE TRIGGER trg_vocab_votes BEFORE INSERT OR UPDATE ON votes
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('vote_value','vote_value');
CREATE TRIGGER trg_vocab_documents BEFORE INSERT OR UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('doc_type','doc_type');
CREATE TRIGGER trg_vocab_media BEFORE INSERT OR UPDATE ON media_assets
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('media_host','host');
CREATE TRIGGER trg_vocab_media_dv BEFORE INSERT OR UPDATE ON media_assets
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('video_date_verification','date_verification');
CREATE TRIGGER trg_vocab_media_disc BEFORE INSERT OR UPDATE ON media_assets
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('media_discovery_method','discovered_via');
CREATE TRIGGER trg_vocab_subjects_obj BEFORE INSERT OR UPDATE ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('objective_outcome','formal_objective_achieved');
CREATE TRIGGER trg_vocab_subjects_gains BEFORE INSERT OR UPDATE ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('collateral_gains','collateral_gains');
CREATE TRIGGER trg_vocab_subject_aliases BEFORE INSERT OR UPDATE ON subject_aliases
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alias_type','alias_type');
CREATE TRIGGER trg_vocab_arguments BEFORE INSERT OR UPDATE ON arguments
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('reason_class','argument_class');
CREATE TRIGGER trg_vocab_segments_kind BEFORE INSERT OR UPDATE ON segments
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('segment_kind','segment_kind');
CREATE TRIGGER trg_vocab_segments_dev BEFORE INSERT OR UPDATE ON segments
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('deviation_kind','deviation_kind');
CREATE TRIGGER trg_vocab_utt_cap BEFORE INSERT OR UPDATE ON utterances
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('actor_capacity','actor_capacity');
CREATE TRIGGER trg_vocab_utt_sid BEFORE INSERT OR UPDATE ON utterances
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('speaker_id_method','speaker_id_method');
CREATE TRIGGER trg_vocab_claims_cur BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('curation_state','curation_state');
CREATE TRIGGER trg_vocab_claims_pol BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('polarity','polarity');
CREATE TRIGGER trg_vocab_claims_dim BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('contested_dimension','contested_dimension');
CREATE TRIGGER trg_vocab_claims_mod BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('modality','modality');
CREATE TRIGGER trg_vocab_claims_act BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('speech_act','speech_act');
CREATE TRIGGER trg_vocab_claims_cap BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('actor_capacity','actor_capacity');
CREATE TRIGGER trg_vocab_claims_src BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('source_type','source_type');
CREATE TRIGGER trg_vocab_claims_span BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('span_unit','span_unit');
CREATE TRIGGER trg_vocab_claims_prec BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('date_precision','asserted_precision');
CREATE TRIGGER trg_vocab_claims_cal BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('calendar_system','asserted_calendar');
CREATE TRIGGER trg_vocab_reasons BEFORE INSERT OR UPDATE ON claim_reasons
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('reason_class','reason_class');
CREATE TRIGGER trg_vocab_cond_origin BEFORE INSERT OR UPDATE ON claim_conditions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('condition_origin','condition_origin');
CREATE TRIGGER trg_vocab_cond_met BEFORE INSERT OR UPDATE ON claim_conditions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('is_met','is_met');
CREATE TRIGGER trg_vocab_relations BEFORE INSERT OR UPDATE ON claim_relations
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('claim_relation','relation');
CREATE TRIGGER trg_vocab_jcitations BEFORE INSERT OR UPDATE ON jurisdiction_citations
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('citation_stance','cited_as');
CREATE TRIGGER trg_vocab_aevents BEFORE INSERT OR UPDATE ON asserted_events
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('asserted_event_class','event_class');
CREATE TRIGGER trg_vocab_aevents_prec BEFORE INSERT OR UPDATE ON asserted_events
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('date_precision','occurred_precision');
CREATE TRIGGER trg_vocab_attest BEFORE INSERT OR UPDATE ON event_attestations
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('attestation_type','attestation_type');
CREATE TRIGGER trg_vocab_decisions_type BEFORE INSERT OR UPDATE ON decisions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('decision_type','decision_type');
CREATE TRIGGER trg_vocab_decisions_out BEFORE INSERT OR UPDATE ON decisions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('decision_outcome','outcome');
CREATE TRIGGER trg_vocab_alt_disp BEFORE INSERT OR UPDATE ON considered_alternatives
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alternative_disposition','disposition');
CREATE TRIGGER trg_vocab_alt_rej BEFORE INSERT OR UPDATE ON considered_alternatives
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('rejection_class','rejection_class');
CREATE TRIGGER trg_vocab_commitments BEFORE INSERT OR UPDATE ON commitments
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('commitment_status','status');
CREATE TRIGGER trg_vocab_coalition_role BEFORE INSERT OR UPDATE ON coalition_members
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('involvement_role','role');
CREATE TRIGGER trg_vocab_fiscal_instr BEFORE INSERT OR UPDATE ON fiscal_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('funding_instrument','funding_instrument');
CREATE TRIGGER trg_vocab_fiscal_src BEFORE INSERT OR UPDATE ON fiscal_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('funding_source','funding_source');
CREATE TRIGGER trg_vocab_fiscal_rec BEFORE INSERT OR UPDATE ON fiscal_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('funding_recurrence','recurrence');
CREATE TRIGGER trg_vocab_fiscal_stat BEFORE INSERT OR UPDATE ON fiscal_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('award_status','award_status');
CREATE TRIGGER trg_vocab_qty_unit BEFORE INSERT OR UPDATE ON quantities
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('quantity_unit','unit');
CREATE TRIGGER trg_vocab_qty_measure BEFORE INSERT OR UPDATE ON quantities
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('quantity_measure','measure');
CREATE TRIGGER trg_vocab_qty_bound BEFORE INSERT OR UPDATE ON quantities
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('quantity_bound','bound');
CREATE TRIGGER trg_vocab_barriers BEFORE INSERT OR UPDATE ON barriers
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('barrier_class','barrier_class');
CREATE TRIGGER trg_vocab_coalitions BEFORE INSERT OR UPDATE ON coalitions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('coalition_type','coalition_type');
CREATE TRIGGER trg_vocab_diffusion BEFORE INSERT OR UPDATE ON program_diffusion
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('diffusion_relation','relation');
CREATE TRIGGER trg_vocab_pages BEFORE INSERT OR UPDATE ON pages
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('page_type','page_type');
CREATE TRIGGER trg_vocab_curation BEFORE INSERT OR UPDATE ON curation_decisions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('curation_decision','decision');
CREATE TRIGGER trg_vocab_review BEFORE INSERT OR UPDATE ON review_queue
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('review_reason','reason');
CREATE TRIGGER trg_vocab_rq BEFORE INSERT OR UPDATE ON research_questions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('research_question_origin','origin');
CREATE TRIGGER trg_vocab_st_status BEFORE INSERT OR UPDATE ON source_targets
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('source_target_status','status');
CREATE TRIGGER trg_vocab_st_doc BEFORE INSERT OR UPDATE ON source_targets
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('doc_type','doc_type');
CREATE TRIGGER trg_vocab_ssl_doc BEFORE INSERT OR UPDATE ON source_search_log
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('doc_type','doc_type');
CREATE TRIGGER trg_vocab_ssl_out BEFORE INSERT OR UPDATE ON source_search_log
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('search_outcome','outcome');
CREATE TRIGGER trg_vocab_covers_period_source BEFORE INSERT OR UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('covers_period_source','covers_period_source');

CREATE TRIGGER trg_vocab_parse_confidence BEFORE INSERT OR UPDATE ON document_sections
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('parse_confidence','parse_confidence');

CREATE TRIGGER trg_vocab_funding_alias_type BEFORE INSERT OR UPDATE ON funding_program_aliases
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alias_type','alias_type');

CREATE TRIGGER trg_vocab_model_confidence BEFORE INSERT OR UPDATE ON figure_data_points
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('model_confidence','model_confidence');

CREATE TRIGGER trg_vocab_org_alias_type BEFORE INSERT OR UPDATE ON org_aliases
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('alias_type','alias_type');

CREATE TRIGGER trg_vocab_subject_kind BEFORE INSERT OR UPDATE ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('subject_kind','subject_kind');

CREATE TRIGGER trg_vocab_mention_method BEFORE INSERT OR UPDATE ON claim_subject_mentions
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('mention_method','method');


-- ══════════════════════════════════════════════════════════════════════════════════════
-- FOLDED-IN MIGRATIONS — vocabularies, terms and their triggers
-- See the matching section in claim_store.sql for why this is here.
-- ══════════════════════════════════════════════════════════════════════════════════════

-- ─── 006_document_sections.sql ───

-- Vocabulary seeds. Semi-open per Rule 1: the trigger proposes rather than rejects, so
-- these are a starting set and not a closed list.
--
-- The VOCABULARY has to be registered before its terms: vocabulary_terms.vocabulary is a
-- foreign key into vocabularies, and inserting terms alone fails with a constraint error
-- rather than quietly creating the vocabulary. is_open FALSE because a parse verdict is a
-- gate -- a new severity should be a deliberate decision, not something extraction invents
-- and the trigger accepts. fallback_term is the value that BLOCKS, so an unrecognised
-- verdict fails closed like every other unknown in this pipeline.
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
    ('parse_confidence',
     'Whether a document section has been checked well enough to extract claims from. '
     'Extraction accepts only ''clean''.',
     FALSE, 'unaudited')
ON CONFLICT DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
    ('parse_confidence', 'unaudited',
     'Default. Nothing has checked this section; extraction refuses it.', 'migration-006'),
    ('parse_confidence', 'clean',
     'Checked and readable. The only value extraction accepts.', 'migration-006'),
    ('parse_confidence', 'suspect',
     'A check fired. May be staged but cannot publish.', 'migration-006'),
    ('parse_confidence', 'known_incomplete',
     'Content is provably missing and is not machine-recoverable -- Year 2''s grant table. '
     'Marked permanently so a partial list is never presented as a whole one.',
     'migration-006')
ON CONFLICT DO NOTHING;

-- ─── 007_period_provenance.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
    ('covers_period_source',
     'How a document''s coverage period was arrived at. A period a human inferred is '
     'evidence of a different kind from one the document printed.',
     FALSE, 'unknown')
ON CONFLICT DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
    ('covers_period_source', 'stated',
     'The document prints the range. Carried verbatim, typos included.', 'migration-007'),
    ('covers_period_source', 'human_estimate',
     'A person inferred it from context. Requires covers_period_note.', 'migration-007'),
    ('covers_period_source', 'unknown',
     'Default. Nothing has established where the period came from.', 'migration-007')
ON CONFLICT DO NOTHING;

-- ─── 009_measure_vocab_and_dates.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
    ('quantity_measure',
     'What a quantity counts, as distinct from the unit it counts in. Two numbers sharing '
     'a unit but not a measure must never be summed.',
     TRUE, 'unspecified')
ON CONFLICT DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
    ('quantity_measure', 'emissions_reduced',    'GHG avoided or cut',        'migration-009'),
    ('quantity_measure', 'emissions_total',      'GHG emitted in a period',   'migration-009'),
    ('quantity_measure', 'energy_saved',         'Energy not consumed',       'migration-009'),
    ('quantity_measure', 'capacity_installed',   'Generation or storage built','migration-009'),
    ('quantity_measure', 'people_served',        'Individuals reached',       'migration-009'),
    ('quantity_measure', 'households_served',    'Households reached',        'migration-009'),
    ('quantity_measure', 'facilities_treated',   'Buildings or sites acted on','migration-009'),
    ('quantity_measure', 'units_deployed',       'Devices, vehicles, trees',  'migration-009'),
    ('quantity_measure', 'participants',         'Attendees or enrollees',    'migration-009'),
    ('quantity_measure', 'cost_saved',           'Money not spent',           'migration-009'),
    ('quantity_measure', 'area_protected',       'Land conserved',            'migration-009'),
    ('quantity_measure', 'organizations_engaged','Partner bodies involved',   'migration-009'),
    ('quantity_measure', 'unspecified',
     'Default. The document states a number whose measure is not determinable.',
     'migration-009')
ON CONFLICT DO NOTHING;

-- The precision of a date a claim INHERITED from its document rather than stated itself.
INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
    ('date_precision', 'reporting_period',
     'The claim states no date; it is dated to the coverage period of the document that '
     'carries it. Not year and not fiscal_year -- reports in this corpus use both and '
     'neither.', 'migration-009')
ON CONFLICT DO NOTHING;

-- ─── 012_funding_programs_and_a2zero_alias.sql ───

-- funding_instrument was declared TEXT with no vocabulary and no terms, so nothing has
-- ever constrained or proposed a value for it. Declared and seeded now that the column
-- next to it means something different.
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('funding_instrument', 'The financial mechanism of an award, not its source or program',
   TRUE, 'other')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, approved_by) VALUES
  ('funding_instrument','block_grant','schema-v0.2'),
  ('funding_instrument','competitive_grant','schema-v0.2'),
  ('funding_instrument','formula_grant','schema-v0.2'),
  ('funding_instrument','mini_grant','schema-v0.2'),
  ('funding_instrument','sponsorship','schema-v0.2'),
  ('funding_instrument','rebate','schema-v0.2'),
  ('funding_instrument','loan','schema-v0.2'),
  ('funding_instrument','revolving_loan','schema-v0.2'),
  ('funding_instrument','appropriation','schema-v0.2'),
  ('funding_instrument','millage_revenue','schema-v0.2'),
  ('funding_instrument','other','schema-v0.2')
ON CONFLICT DO NOTHING;

-- ─── 013_snapshot_provenance.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('provenance_source', 'How a provenance value was established', TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('provenance_source','retrieved','downloaded by the pipeline; URL and timestamp are recorded','schema-v0.2'),
  ('provenance_source','local_file','hashed from a file on disk with no retrieval record','schema-v0.2'),
  ('provenance_source','human_supplied','a person stated it','schema-v0.2'),
  ('provenance_source','unknown','not established','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_documents_snapshot BEFORE INSERT OR UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('provenance_source','snapshot_source');

-- ─── 014_human_verdict.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('human_verdict', 'A person''s judgement on whether a section may be extracted',
   TRUE, 'not_reviewed')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('human_verdict','approved','a person read this against the source and vouches for it','schema-v0.2'),
  ('human_verdict','approved_with_caveats','usable, but the note states a known limit','schema-v0.2'),
  ('human_verdict','rejected','a person read it and it is not usable','schema-v0.2'),
  ('human_verdict','not_reviewed','no person has looked','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_sections_human BEFORE INSERT OR UPDATE ON document_sections
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('human_verdict','human_verdict');

-- ─── 015_footnotes.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('marker_evidence', 'How a footnote marker was established', TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('marker_evidence','printed','the marker survived the conversion as a legible numeral','schema-v0.2'),
  ('marker_evidence','second_read','recovered from a second independent read of the pixels','schema-v0.2'),
  ('marker_evidence','text_layer','recovered from the PDF text layer','schema-v0.2'),
  ('marker_evidence','human','a person read the page and said so','schema-v0.2'),
  ('marker_evidence','unknown','not established','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_footnote_ref BEFORE INSERT OR UPDATE ON footnote_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('marker_evidence','marker_evidence');

-- ─── 017_fiscal_direction.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('fiscal_direction', 'Which way money moved relative to the actor making the claim',
   TRUE, 'unknown')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('fiscal_direction','received','money awarded to or won by the actor','schema-v0.2'),
  ('fiscal_direction','disbursed','money the actor granted or paid out to others','schema-v0.2'),
  ('fiscal_direction','saved','costs avoided or savings claimed; no money changed hands','schema-v0.2'),
  ('fiscal_direction','spent','money the actor spent on its own activity','schema-v0.2'),
  ('fiscal_direction','authorized','committed or appropriated, not yet moved','schema-v0.2'),
  ('fiscal_direction','unknown','the text does not say','schema-v0.2')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_fiscal_direction BEFORE INSERT OR UPDATE ON fiscal_references
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('fiscal_direction','direction');

-- ─── 019_quantity_unit_vocabulary.sql ───

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('quantity_unit','percent','a proportion; unit_basis says of what','schema-v0.3'),
  ('quantity_unit','metric_tons_co2e','mass of CO2 equivalent','schema-v0.3'),
  ('quantity_unit','metric_tons','mass, NOT asserted to be carbon','schema-v0.3'),
  ('quantity_unit','miles','distance','schema-v0.3'),
  ('quantity_unit','square_feet','floor or land area','schema-v0.3'),
  ('quantity_unit','years','duration in years','schema-v0.3'),
  ('quantity_unit','days','duration in days or weeks','schema-v0.3'),
  ('quantity_unit','ratio','a dimensionless ratio','schema-v0.3'),
  ('quantity_unit','other','the text gave no usable unit','schema-v0.3'),
  ('quantity_unit','kWh','energy','schema-v0.3')
ON CONFLICT DO NOTHING;

-- ─── 020_initiative_layer.sql ───

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('subject_kind', 'What sort of thing a subject is', TRUE, 'other')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('subject_kind','plan','a whole plan or programme, e.g. A2ZERO','schema-v0.3'),
  ('subject_kind','strategy','a top-level division of a plan','schema-v0.3'),
  ('subject_kind','initiative','a named project or programme that delivers work','schema-v0.3'),
  ('subject_kind','place','a geography: a neighbourhood, corridor, facility','schema-v0.3'),
  ('subject_kind','topic','a subject of discussion that is not a project','schema-v0.3'),
  ('subject_kind','other','none of the above','schema-v0.3')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_subject_kind BEFORE INSERT OR UPDATE ON subjects
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('subject_kind','subject_kind');

-- ---------------------------------------------------------------- who does what
-- coalition_members already carries person/org/body, a role and a claim_id for provenance,
-- and coalitions already hangs off a subject. That is the actors-with-roles requirement
-- entire; what was missing is a controlled vocabulary for `role`, without which it becomes
-- the free-text field quantity_unit was.
INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('involvement_role', 'How an actor is involved in an initiative', TRUE, 'participant')
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('involvement_role','lead','accountable for delivery','schema-v0.3'),
  ('involvement_role','co_lead','shares accountability','schema-v0.3'),
  ('involvement_role','implementer','does the work without owning it','schema-v0.3'),
  ('involvement_role','community_partner','a community organisation collaborating','schema-v0.3'),
  ('involvement_role','funder','supplies money','schema-v0.3'),
  ('involvement_role','regulator','holds approval or oversight authority','schema-v0.3'),
  ('involvement_role','beneficiary','the work is done for them','schema-v0.3'),
  ('involvement_role','participant','involved, role unstated','schema-v0.3')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE TRIGGER trg_vocab_coalition_role BEFORE INSERT OR UPDATE ON coalition_members
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('involvement_role','role');

-- ─── 022_section_topic.sql ───
-- 022: give section_topic a vocabulary, and a first member that the CAP needs.
--
-- WHY NOW. document_sections.section_topic has existed since migration 006, carries the
-- comment "vocab: section_topic", and had NO vocabulary and ZERO populated rows. It was a
-- column waiting for a purpose, and the CAP supplies one.
--
-- THE PROBLEM IT SOLVES, MEASURED. Appendix 5, "List of Ideas Considered for A2ZERO", is 35
-- sections of ideas the City received and did NOT adopt as Actions -- among them "Geothermal
-- districts", which later became a central municipal venture. Extraction treated them
-- arbitrarily: 29 sections produced nothing at all, 7 produced 69 claims, from identical
-- content. Worse, the 69 came out `hypothetical`, which is the same modality an Action's
-- Vision block carries. So "It is 2030, and we have reached carbon neutrality" -- a
-- commitment the Plan makes -- and "Create carbon tax" -- an idea the Plan declined -- are
-- indistinguishable in the store.
--
-- MODALITY IS THE WRONG PLACE TO FIX IT. modality is assertion STRENGTH: asserted, hedged,
-- hypothetical, attributed_to_other. Whether an idea was adopted is not a property of how
-- strongly the City said it; adding a `considered` modality would conflate two axes and
-- corrupt every query that already groups by it. The frame belongs to the SECTION, which is
-- what section_topic is for.
--
-- The document says this about itself, and the claim is already stored on the appendix's
-- own intro section: "The Plan presented above includes the ideas evaluated to be the most
-- impactful ... The list of ideas below serves to document the full list of ideas received,
-- and to be turned to should adjustments to the Plan be required."

INSERT INTO vocabularies (name, description, is_open, fallback_term) VALUES
  ('section_topic',
   'What a section IS, where that changes how its claims should be read. Not what the '
   'section is ABOUT -- that is subject_id. A section with no topic is the normal case.',
   TRUE, NULL)
ON CONFLICT (name) DO NOTHING;

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('section_topic', 'ideas_considered',
   'Options the author received and recorded but did not adopt. The claims inside are real '
   'assertions that these ideas were RAISED -- never that they were planned or funded. '
   'CAP-2020 Appendix 5 is the founding case: 35 sections, including the district geothermal '
   'idea that was not made an Action in 2020.',
   'schema-v0.4'),
  ('section_topic', 'timeline',
   'A dated sequence of steps. Its claims are commitments with a year attached, and the year '
   'is the claim''s world time rather than the document''s.',
   'schema-v0.4'),
  ('section_topic', 'assumptions',
   'What a projection depends on. Its claims are conditions, not outcomes -- which is why '
   'they extract as hypothetical and must not be read as commitments.',
   'schema-v0.4'),
  ('section_topic', 'roster',
   'A list of people, bodies or organisations. Names to resolve, not assertions about the '
   'world; the annual reports'' staff footers are the existing example.',
   'schema-v0.4'),
  ('section_topic', 'engagement_log',
   'A dated record of meetings, events or outreach. Each entry asserts that a thing happened '
   'on a date, which is exactly a claim, but none of them asserts a policy.',
   'schema-v0.4')
ON CONFLICT DO NOTHING;

-- NEVER REJECTS, like every other vocabulary here. fallback_term is NULL deliberately: an
-- unrecognised topic is stored as given and raises a proposal, because guessing a frame is
-- worse than recording an unknown one.
CREATE OR REPLACE TRIGGER trg_vocab_section_topic
    BEFORE INSERT OR UPDATE ON document_sections
    FOR EACH ROW EXECUTE FUNCTION enforce_vocabulary('section_topic', 'section_topic');

-- ─── 010_undo_duplicate_measures.sql ───
--
-- NOT DATA, ONTOLOGY MAINTENANCE. Folding the migrations in classified INSERT/UPDATE/DELETE
-- as data and left them behind -- right for a seeded alias row, wrong for this: migration 009
-- introduced nine quantity_measure terms that duplicated established ones
-- (`capacity_installed` beside `installed_capacity`, `emissions_reduced` and
-- `emissions_total` beside `emissions`), and 010 exists to remove them. Without it a rebuilt
-- database carries eight dead terms the live one does not, and an extractor could pick either
-- spelling.
--
-- Placed at the end so it applies after every INSERT above, whatever order they arrived in.
DELETE FROM vocabulary_terms
 WHERE vocabulary = 'quantity_measure'
   AND approved_by = 'migration-009'
   AND term IN ('capacity_installed', 'cost_saved', 'participants', 'people_served',
                'energy_saved', 'emissions_reduced', 'emissions_total',
                'organizations_engaged', 'unspecified');
