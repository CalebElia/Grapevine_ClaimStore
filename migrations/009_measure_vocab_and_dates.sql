-- 009 — make `measure` mean something, let a claim be dated, and fix coalition_members.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- quantity_measure HAD NO TERMS AT ALL.
--
-- quantities.measure is declared `vocab: quantity_measure` and that vocabulary was never
-- created, so the first extraction run wrote 'other' five times against a term that does
-- not exist. unit and measure answer different questions and the difference is what makes
-- numbers comparable:
--
--     unit     what you count IN        metric tons · households · MW · USD
--     measure  what is being COUNTED    emissions_reduced · households_served
--
-- "113 metric tons of carbon emissions reduced" and "2.1 million metric tons of
-- community-wide emissions" share a unit and must never be summed, because one is a
-- reduction and the other is a total. Only `measure` can say so.
--
-- Open, because this list will not survive contact with a docket or a minutes corpus and
-- the trigger should propose rather than reject.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- A CLAIM WITH NO DATE IS INVISIBLE FOREVER.
--
-- All 17 claims from Year 3 section 5 carried asserted_start IS NULL, because no sentence
-- states when it happened -- and the report covers July 2022 to June 2023, so every one of
-- them IS dated, at the document's precision. Chronology is the join key across every
-- source in this store and timeline queries compare INTERVALS, so a NULL start cannot
-- participate in any of them: not wrong, simply absent.
--
-- `reporting_period` is added to date_precision to say exactly what such a date is. It is
-- not `year` (Year 5 runs June to May) and not `fiscal_year` (only Years 1-4 do), and
-- pretending otherwise would make a claim look more precisely dated than it is.
--
-- ────────────────────────────────────────────────────────────────────────────────────
-- coalition_members COULD NAME A PERSON AND AN ORG AT ONCE.
--
-- Flagged in Collin's review and deferred. A membership row is one member: a person, an
-- org or a body. Permitting two makes "who was in this coalition" ambiguous per row, and
-- this table is about to matter -- it is how a claim naming both the City and the Ann
-- Arbor Housing Commission records the second actor.

BEGIN;

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

-- One membership row names exactly one member.
DELETE FROM coalition_members
 WHERE num_nonnulls(person_id, org_id, body_id) <> 1;
ALTER TABLE coalition_members DROP CONSTRAINT IF EXISTS coalition_members_one_member;
ALTER TABLE coalition_members ADD CONSTRAINT coalition_members_one_member
    CHECK (num_nonnulls(person_id, org_id, body_id) = 1);

COMMIT;
