-- 019: make quantity_unit a vocabulary again, and give the counted noun its own home.
--
-- THE MECHANISM FAILURE, EXACTLY. `quantity_unit` was declared with fallback_term NULL. The
-- enforce_vocabulary trigger logs an unknown term and then "substitutes the fallback IF ONE
-- IS DEFINED" -- so with no fallback the raw value passed straight through. The column has
-- been free text since the schema was written, and the only visible symptom was a proposal
-- queue nobody could read: 160 of 218 pending proposals were quantity_unit values.
--
-- quantity_measure, declared the same day with fallback_term 'other', has 23. One NULL is
-- the whole difference between a controlled vocabulary and a text column that logs.
--
-- WHAT WAS ACTUALLY WRONG WITH THE VALUES. The extraction put the COUNTED NOUN in `unit` --
-- "Direct Current Fast Chargers (DCFCs)", "community organization, collaborators, and
-- partners" -- because the prompt said `"unit" is what you count IN (metric tons,
-- households, MW, acres)` and `households` is a noun. Real units appeared whenever the text
-- offered one, so the field degraded rather than failed.
--
-- THE DETAIL IS THE VALUABLE PART. "18 air quality monitors" beats "18 things". But
-- unit='air quality monitors' will never aggregate with unit='AQMesh monitors'. So the two
-- facts are separated rather than traded off: `unit` carries the DIMENSION and is what you
-- GROUP BY; `unit_basis` carries WHAT WAS COUNTED, verbatim and uncontrolled. unit_basis
-- already existed for precisely this -- "what a % or count is OF" -- and was populated zero
-- times in 388 rows. Same shape as funder_name_text beside awarding_org_id: one field to
-- join on, one that keeps what the document said.
UPDATE vocabularies SET fallback_term = 'other' WHERE name = 'quantity_unit';

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

-- DEPRECATED, NOT DELETED. A term that was approved and is now superseded points at its
-- replacement, so a stored value can still be read and re-mapped. Deleting it would make
-- every existing row referencing it unexplainable.
UPDATE vocabulary_terms SET deprecated_by_term = 'percent'
  WHERE vocabulary='quantity_unit' AND term = 'pct';
UPDATE vocabulary_terms SET deprecated_by_term = 'metric_tons_co2e'
  WHERE vocabulary='quantity_unit' AND term = 'tCO2e';
UPDATE vocabulary_terms SET deprecated_by_term = 'metric_tons'
  WHERE vocabulary='quantity_unit' AND term = 'tons';
-- These two are NOUNS that were seeded as units, which is the same mistake the extraction
-- then made at scale. They belong in unit_basis.
UPDATE vocabulary_terms SET deprecated_by_term = 'count'
  WHERE vocabulary='quantity_unit' AND term IN ('households','people');

COMMENT ON COLUMN quantities.unit IS
  'The DIMENSION of the measurement, from a closed vocabulary. This is what you GROUP BY. '
  'It must never assert more than the text: bare tons stay metric_tons, because "tons of '
  'material" diverted from landfill is not CO2e.';
COMMENT ON COLUMN quantities.unit_basis IS
  'WHAT WAS COUNTED or what a percentage is OF, verbatim and never normalised: "air quality '
  'monitors", "Direct Current Fast Chargers (DCFCs)". This is the detail that makes a row '
  'worth reading; `unit` is the part that makes rows comparable.';
