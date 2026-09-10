-- 031: a quantity_measure for what a grid is made of.
--
-- "In 2022, over 54% of DTE Electric's Fuel Mix used to supply electricity to Ann Arbor was
-- generated using coal" had nowhere to go. It is not adoption_rate (that is uptake of a
-- programme), not emissions (this is fuel share, and the emissions follow from it rather than
-- being it), and not goal_target (it is observed, not aimed at). It landed on 'other' and the
-- extractor proposed `energy` ten times.
--
-- NAMED FOR WHAT IS MEASURED, NOT THE TOPIC IT SITS NEAR. `energy` would have been a bucket:
-- consumption, savings, capacity and price are all "energy" and are all already covered by
-- other terms. generation_mix says the quantity is a SHARE OF GENERATION, which is what makes
-- 54%-coal comparable to a later 30%-coal and not comparable to a 54% participation rate.
--
-- Approved by the curator, which is the only way this vocabulary grows: the trigger proposes,
-- a person decides.

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('quantity_measure', 'generation_mix',
   'The share of electricity generation attributable to a fuel or source -- "54% of the fuel '
   'mix was coal". Distinct from adoption_rate, which is uptake of a programme, and from '
   'emissions, which is what the mix causes rather than the mix itself.',
   'caleb')
ON CONFLICT DO NOTHING;

UPDATE quantities SET measure = 'generation_mix'
 WHERE measure = 'other' AND unit = 'percent'
   AND verbatim ILIKE '%54%' AND claim_id IN (
       SELECT id FROM claims WHERE verbatim ILIKE '%fuel mix%');

UPDATE vocabulary_proposals
   SET status = 'approved_as', resolved_by = 'migration-031 -> generation_mix',
       resolved_at = now()
 WHERE vocabulary = 'quantity_measure' AND proposed_term = 'energy' AND status = 'pending';
