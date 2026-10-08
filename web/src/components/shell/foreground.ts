// UI interaction lifetime only; no domain request or navigation side effects.
let revision = 0;
export const getForegroundRevision = () => revision;
export const advanceForegroundRevision = () => {
  revision += 1;
};
