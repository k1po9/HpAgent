/** Pure critically damped progress. Direction changes retain both position and velocity. */
export class MercuryTimeline {
  progress: number;
  velocity = 0;
  target: 0 | 1;
  constructor(
    start: 0 | 1,
    readonly omega = 8.5,
    readonly hostTempo = 1,
  ) {
    this.progress = start;
    this.target = start;
  }
  goto(target: 0 | 1) {
    this.target = target;
  }
  advance(seconds: number) {
    // Shorten the outgoing host tail and incoming host lead, retaining the main flow speed.
    const dt =
      Math.min(1 / 30, Math.max(0, seconds)) * (this.progress >= 0.98 ? this.hostTempo : 1);
    const delta = this.progress - this.target;
    const transient = (this.velocity + this.omega * delta) * dt;
    const decay = Math.exp(-this.omega * dt);
    this.progress = Math.min(1, Math.max(0, this.target + (delta + transient) * decay));
    this.velocity = (this.velocity - this.omega * transient) * decay;
    if (this.settled) {
      this.progress = this.target;
      this.velocity = 0;
    }
    return this.progress;
  }
  get settled() {
    return Math.abs(this.progress - this.target) < 0.003 && Math.abs(this.velocity) < 0.025;
  }
  finish(target: 0 | 1 = this.target) {
    this.target = target;
    this.progress = target;
    this.velocity = 0;
  }
}

/** Reading controls and conversation switches share one motion clock. */
export function createMercuryTimeline(start: 0 | 1, speed: number) {
  return new MercuryTimeline(start, 8.5 * speed, 2);
}
