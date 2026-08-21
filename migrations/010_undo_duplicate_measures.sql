-- 010 — remove the measure terms 009 added that duplicate schema-v0.2's.
--
-- I asserted quantity_measure "had no terms at all" and seeded twelve. It had twelve
-- already, from schema-v0.2, and 'other' -- which the extraction had chosen -- was a
-- legitimate member of them. The claim came from querying vocabulary='measure', which is
-- the COLUMN name; the vocabulary is 'quantity_measure'. An empty result from the wrong
-- key looked exactly like an empty vocabulary.
--
-- What 009 actually did was create a parallel set: installed_capacity beside
-- capacity_installed, savings beside cost_saved, participants twice. A controlled
-- vocabulary with two words for one thing is worse than a coarse one, because now every
-- writer has to guess which, and every reader has to check both.
--
-- So the duplicates go and the existing names win, being older and already referenced.
-- Four terms survive because schema-v0.2 has nothing for them, and this corpus needs each:
--
--     households_served     "helping 19 households make improvements"
--     facilities_treated    "audits at four municipal buildings"
--     units_deployed        "5,750 trees planted", "50 more EV chargers"
--     area_protected        "7,600 acres of farmland permanently protected"
--
-- STILL OPEN, AND NOT DECIDED HERE: `emissions` does not distinguish a REDUCTION from a
-- TOTAL. "113 metric tons of carbon emissions" reduced and "2.1 million metric tons"
-- community-wide are both `emissions` in the same unit, and summing them is meaningless.
-- That is a real gap in an established vocabulary, and narrowing an existing term is a
-- curator's decision rather than a migration's.

BEGIN;

DELETE FROM vocabulary_terms
 WHERE vocabulary = 'quantity_measure'
   AND approved_by = 'migration-009'
   AND term IN ('capacity_installed', 'cost_saved', 'participants', 'people_served',
                'energy_saved', 'emissions_reduced', 'emissions_total',
                'organizations_engaged', 'unspecified');

COMMIT;
