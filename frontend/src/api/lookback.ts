/**
 * The lookback windows an investigation may select (#23).
 *
 * This mirrors the backend's single source of truth,
 * `ALLOWED_LOOKBACK_HOURS` in `app/investigations/schemas.py`
 * (#9). Do not retype the list here — derive the type from
 * the constant so the client cannot drift from the API.
 */

export const LOOKBACK_HOURS = [1, 4, 8, 24] as const;

export type LookbackHours = (typeof LOOKBACK_HOURS)[number];

/** Whether ``value`` is one of the allowed lookback windows. */
export function isLookbackHours(value: number): value is LookbackHours {
  return (LOOKBACK_HOURS as readonly number[]).includes(value);
}
