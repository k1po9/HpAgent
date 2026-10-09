import { createContext } from "react";

// Portaled modal surfaces must not enter the browser top layer during entry.
export const EntryPendingContext = createContext(false);
