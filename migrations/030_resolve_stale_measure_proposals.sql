-- 030: close out 39 vocabulary proposals that describe data no longer in the store, and one
-- quantity that was never a quantity.
--
-- WHAT THESE PROPOSALS ARE NOT. Read as a list of terms -- 'State of Michigan',
-- 'SEMCOG Carbon Reduction Program', 'households helped make health, safety, and quality of
-- life improvements' -- they look like an extractor putting proper nouns and prose into
-- categorical columns. They are not. Every one was logged on 2026-08-21 against
-- fiscal_references ids 4-16 and quantities rows that no longer exist; the corpus was
-- re-extracted afterwards and current ids start at 115 and 141. Of 16 funding_source
-- proposals, ZERO point at a live row; of 23 quantity_measure proposals, one does.
--
-- The current extractor already separates the three facts properly: the category goes to
-- funding_source, the name the document printed to funder_name_text, the resolved body to
-- awarding_org_id, and the programme to program_id. What was missing was the step BETWEEN
-- them -- nothing derived the category from the resolved body, so 45 of 75 rows sat on the
-- fallback while the store knew the money came from the EPA. That is now
-- resolve_orgs.backfill_funding_source, driven by registries/ann_arbor/funder_categories.json.
--
-- quantity_measure = 'other' IS MOSTLY CORRECT and is left alone. 'Forty-four actions', 'six
-- core strategies', 'one of the three core tenants' are structural facts about the plan, not
-- measurements any of adoption_rate / emissions / participants / units_deployed describes.
-- Widening the vocabulary to swallow them would make 'measure' mean "something numeric was
-- said", which is what `unit` already covers.
--
-- LEAVING THEM PENDING HAS A COST. scripts/status.sh reports pending proposals as live drift,
-- so 39 answered-by-events rows would keep presenting as work outstanding, and the real
-- signal -- a NEW term appearing -- would be buried under them.

UPDATE vocabulary_proposals
   SET status = 'stale', resolved_by = 'migration-030', resolved_at = now()
 WHERE status = 'pending'
   AND vocabulary IN ('funding_source', 'quantity_measure')
   AND NOT EXISTS (SELECT 1 FROM fiscal_references f
                    WHERE vocabulary_proposals.entity_table = 'fiscal_references'
                      AND f.id = vocabulary_proposals.entity_id)
   AND NOT EXISTS (SELECT 1 FROM quantities q
                    WHERE vocabulary_proposals.entity_table = 'quantities'
                      AND q.id = vocabulary_proposals.entity_id);

-- AN ORDINAL IN A NAME IS NOT A DURATION. "Our 51st-annual Earth Day celebration" was stored
-- as 51 years. The verbatim is retained on the claim; only the false measurement goes, because
-- a timeline query summing durations would otherwise pick up a birthday.
DELETE FROM quantities
 WHERE unit = 'years' AND value_low = 51
   AND verbatim ILIKE '%51st%annual%';
