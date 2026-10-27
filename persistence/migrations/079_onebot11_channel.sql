-- No legacy-data rewrite: old channel targets must be explicitly inventoried
-- and removed before this migration. Preserve the original migration chain.
ALTER TABLE delivery_targets DROP CONSTRAINT delivery_targets_channel_check;
ALTER TABLE delivery_targets ADD CONSTRAINT delivery_targets_channel_check
 CHECK (channel IN ('web','onebot11','official_qq'));
