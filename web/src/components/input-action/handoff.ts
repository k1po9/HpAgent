import { useSyncExternalStore } from "react";
import type { Rect } from "./geometry";
export type Ticket = {
  id: number;
  account: string;
  conversation: string;
  previousRun: string | null;
  run: string | null;
  navigation: number;
  generation?: number;
  source: Rect;
};
export type RelayPhase = "awaiting" | "travelling" | "forming";
let sequence = 0;
let current: { ticket: Ticket; phase: RelayPhase } | null = null;
const listeners = new Set<() => void>();
const notify = () => [...listeners].forEach((fn) => fn());
export const handoffSnapshot = () => current;
export function armHandoff(scope: Omit<Ticket, "id" | "run" | "source">, source: Rect | null) {
  current = source
    ? { ticket: { ...scope, source, id: ++sequence, run: null }, phase: "awaiting" }
    : null;
  notify();
  return current?.ticket.id;
}
export function confirmHandoff(
  id: number | undefined,
  run: string | undefined,
  generation?: number,
) {
  if (!current || current.ticket.id !== id || !run || run === current.ticket.previousRun) return;
  current = { ...current, ticket: { ...current.ticket, run, generation } };
  notify();
}
export function phaseHandoff(id: number, phase: RelayPhase) {
  if (current?.ticket.id !== id) return;
  current = { ...current, phase };
  notify();
}
export function cancelHandoff(id?: number) {
  if (id !== undefined && current?.ticket.id !== id) return;
  current = null;
  notify();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function useHandoff() {
  return useSyncExternalStore(subscribe, handoffSnapshot, () => null);
}

/** A blocked edit of an uncertain intent must not mint a new visual submission. */
export function leaseForSubmission(
  scope: Omit<Ticket, "id" | "run" | "source">,
  source: Rect | null,
  pending: boolean,
) {
  if (!pending) return armHandoff(scope, source);
  const ticket = current?.ticket;
  return ticket?.account === scope.account &&
    ticket.conversation === scope.conversation &&
    ticket.navigation === scope.navigation
    ? ticket.id
    : undefined;
}
