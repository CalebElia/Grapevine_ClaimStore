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
