import { createContext } from "react";

export const TaskControlContext = createContext(true);
export const TaskEditContext = createContext<(() => void) | undefined>(undefined);
export const TaskFieldContext = createContext<string | undefined>(undefined);
