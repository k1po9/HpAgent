import type { HpWorkEvent } from "../api/types";
import { openSseStream } from "./sseClient";

const activeStops = new Set<() => void>();
export function disposeWorkFeeds() {
  activeStops.forEach((stop) => stop());
  activeStops.clear();
}

/** Business events have a PostgreSQL cursor, independent of chat message sequence. */
export function subscribeWork(
  workId: string,
  cursor: () => number,
  receive: (event: HpWorkEvent) => void,
  recover: () => Promise<void>,
) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let wake: (() => void) | undefined;
  const stop = () => {
    controller.abort();
    if (timer) clearTimeout(timer);
    wake?.();
    activeStops.delete(stop);
  };
  activeStops.add(stop);
  const run = async () => {
    while (!controller.signal.aborted) {
      try {
        await openSseStream(`/api/v1/works/${workId}/events/stream?after=${cursor()}`, {
          signal: controller.signal,
          onFrame(frame) {
            if (controller.signal.aborted || frame.event !== "work.event") return;
            const event = JSON.parse(frame.data) as HpWorkEvent;
            if (event.work_id === workId && event.event_seq > cursor()) receive(event);
          },
        }).done;
      } catch {
        if (controller.signal.aborted) return;
        await recover().catch(() => undefined);
      }
      if (!controller.signal.aborted)
        await new Promise<void>((resolve) => {
          wake = resolve;
          timer = setTimeout(resolve, 3000);
        });
    }
  };
  void run();
  return stop;
}
