/** Stage advances only after this attempt's preceding engineering checks pass. */
export type MercuryStage = "points" | "phase" | "silver";
export const MERCURY_STAGE: MercuryStage = "silver";
export const MERCURY_ENABLED = true;
