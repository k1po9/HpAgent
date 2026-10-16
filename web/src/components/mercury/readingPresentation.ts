import { createContext, useContext } from "react";
import type { HpRun } from "../../api/types";

/** A retiring/rebuilding real surface is inert; defer its optional discovery queries. */
export const ReadingPresentation = createContext<{ retiring: boolean; run: HpRun | null }>({
  retiring: false,
  run: null,
});
export const useReadingPresentation = () => useContext(ReadingPresentation);
