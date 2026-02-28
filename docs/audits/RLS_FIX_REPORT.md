# RLS Column Mismatch Fixes

## Date: Feb 28, 2026
## Status: COMPLETE

4 RLS policies had wrong column names. All fixed via Supabase Management API.

### Policies Created (Corrected):

1. **invoices_client_own** (invoices table)
   - Was using: `contact_id`
   - Fixed to: `customer_id` (actual column name)
   - Policy: `USING (customer_id = public.get_contact_id())`

2. **payments_client_own** (payments table)
   - Was using: `customer_id`
   - Fixed to: `contact_id` (actual column name)
   - Policy: `USING (contact_id = public.get_contact_id())`

3. **conversations_participant_access** (conversations table)
   - Was using: `participants` array
   - Fixed to: `contact_id` (actual column name)
   - Policy: `USING (contact_id = public.get_contact_id())`

4. **activity_log_own_read** (activity_log table)
   - Was using: `user_id`
   - Fixed to: `actor_id` (actual column name)
   - Policy: `USING (actor_id = public.get_contact_id())`

### Verification
All 4 policies created successfully via Supabase Management API.
