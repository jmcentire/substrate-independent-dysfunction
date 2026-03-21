-- Platform Defaults Seed Data
-- These are the base-level settings applied across the entire platform.

INSERT INTO platform_defaults (settings) VALUES ('{
  "check_in_time": "16:00",
  "check_out_time": "11:00",
  "min_stay_nights": 1,
  "max_stay_nights": 30,
  "cancellation_policy": "moderate",
  "instant_book": false,
  "cleaning_fee_enabled": true,
  "currency": "USD",
  "timezone": "America/New_York",
  "pet_policy": "not_allowed",
  "smoking_policy": "not_allowed",
  "party_policy": "not_allowed",
  "quiet_hours_start": "22:00",
  "quiet_hours_end": "08:00",
  "max_occupancy_multiplier": 2,
  "damage_deposit_enabled": true,
  "damage_deposit_amount": 500,
  "auto_approve_reviews": false,
  "notification_preferences": {
    "booking_confirmed": true,
    "booking_cancelled": true,
    "check_in_reminder": true,
    "check_out_reminder": true,
    "review_request": true
  }
}'::jsonb)
ON CONFLICT DO NOTHING;

-- Default brand
INSERT INTO brands (name, slug, settings) VALUES (
  'Wander',
  'wander',
  '{
    "cancellation_policy": "flexible",
    "instant_book": true,
    "check_in_time": "15:00",
    "check_out_time": "11:00",
    "pet_policy": "allowed_with_fee"
  }'::jsonb
)
ON CONFLICT (slug) DO NOTHING;
