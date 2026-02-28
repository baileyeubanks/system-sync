-- F-006 Follow-up: Add CHECK constraint to prevent invalid role values
-- Also ensure phone/email are populated for all active crew
-- Run at: https://supabase.com/dashboard/project/briokwdoonawhxisbydy/sql/new

-- 1. Normalize existing roles to lowercase
UPDATE crew_members SET role = LOWER(TRIM(role)) WHERE role IS NOT NULL;

-- 2. Add CHECK constraint (only allows 'admin' or 'crew')
ALTER TABLE crew_members
  DROP CONSTRAINT IF EXISTS crew_members_role_check;

ALTER TABLE crew_members
  ADD CONSTRAINT crew_members_role_check
  CHECK (role IN ('admin', 'crew'));

-- 3. Set default for new rows
ALTER TABLE crew_members
  ALTER COLUMN role SET DEFAULT 'crew';

-- 4. Show any crew members missing phone or email
SELECT id, name, role, phone, email
FROM crew_members
WHERE status = 'active'
  AND (phone IS NULL OR phone = '' OR email IS NULL OR email = '');
