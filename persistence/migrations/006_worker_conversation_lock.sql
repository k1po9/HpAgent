SET search_path TO hpagent, public;
-- Dispatcher/Reconciler obey the global Conversation -> Run lock order.
-- PostgreSQL requires UPDATE privilege for SELECT ... FOR UPDATE.
GRANT UPDATE ON conversations TO hpagent_worker;
