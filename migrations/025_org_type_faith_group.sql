-- 025: one new org_type term, and the reason the other sixteen are NOT terms.
--
-- 81 of 142 orgs were typed 'other' and seventeen org_type proposals sat pending, because
-- pipeline/resolve_orgs passed the wiki's `actor-type` straight to the column. The wiki
-- writes kebab-case and finer-grained -- government-office, city-department, university,
-- labor-union -- while the store's vocabulary is snake_case and coarser, so only nonprofit,
-- business and utility happened to land and everything else hit the fallback.
--
-- SIXTEEN OF THEM ARE SPELLINGS, NOT CATEGORIES. Approving them would leave the store with
-- two dialects for one idea: `government-office` beside `government`, `company` beside
-- `business`, `university` beside `academic`. That is the same fragmentation as keying the
-- CAP with doc_type='cap' when 'plan' was already approved. They are translated at the
-- boundary instead -- registries/ann_arbor/org_types.json -- so the trigger never sees them.
--
-- ONE IS A REAL CATEGORY. A faith organisation is not a funding structure, and 'nonprofit'
-- says only that. Ann Arbor's CAP names Places of Worship alongside Homes, Businesses and
-- Schools in Strategy 3: they are their own route to residents, which is the thing a query
-- would want to select on.

INSERT INTO vocabulary_terms (vocabulary, term, description, approved_by) VALUES
  ('org_type', 'faith_group',
   'A congregation or faith organisation. Distinct from nonprofit because the CAP treats '
   'places of worship as their own engagement channel, not as a funding structure.',
   'schema-v0.5')
ON CONFLICT DO NOTHING;

-- The sixteen spellings are now translated before insert, so their proposals are answered.
UPDATE vocabulary_proposals
   SET status = 'mapped', resolved_by = 'registries/ann_arbor/org_types.json',
       resolved_at = now()
 WHERE vocabulary = 'org_type' AND status = 'pending';
