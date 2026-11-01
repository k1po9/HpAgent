// SPDX-License-Identifier: AGPL-3.0-or-later
// Ported from rhine-lock frontend/src/n8ao.d.ts at a4df68c. See web/THIRD_PARTY_NOTICES.md.
declare module "n8ao" {
  import type { Camera, Scene } from "three";
  import { Pass } from "postprocessing";
  export class N8AOPostPass extends Pass {
    constructor(scene: Scene, camera: Camera, width?: number, height?: number);
    configuration: Record<string, number | boolean>;
    setQualityMode(mode: "Performance" | "Low" | "Medium" | "High" | "Ultra"): void;
  }
}
