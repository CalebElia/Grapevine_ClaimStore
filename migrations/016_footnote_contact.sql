-- 016: the person a footnote names, resolved to the person registry.
--
-- These seven footnotes each name the City officer responsible for one A2ZERO strategy.
-- Resolving the name to a persons row is what turns "a string in a page-foot block" into a
-- fact you can join: every strategy Missy Stults is accountable for, across every document
-- that says so.
--
-- SIX OF THE SEVEN WERE ALREADY IN THE STORE, imported from Legistar. Creating fresh rows
-- for them would have split each officer into two identities -- one that votes in council
-- records and one that answers questions about a strategy -- which is precisely the harm
-- `persons` exists to prevent. The seventh is the interesting one: the report writes "Missy
-- Stults" where Legistar holds "Melissa Stults". That is a nickname, and person_aliases is
-- where nicknames go; it is not a reason for a second row.
ALTER TABLE footnotes ADD COLUMN IF NOT EXISTS contact_person_id INT REFERENCES persons(id);

COMMENT ON COLUMN footnotes.contact_person_id IS
  'The person this footnote names as a contact, resolved to the person registry. NULL when '
  'the name could not be resolved to exactly one person -- a footnote attributed to the '
  'wrong officer is worse than one attributed to nobody.';
