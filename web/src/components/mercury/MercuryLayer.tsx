import { createPortal } from "react-dom";
import type { RefObject } from "react";
export function MercuryLayer({
  canvas,
  maskPath,
  maskId,
  active,
}: {
  canvas: RefObject<HTMLCanvasElement | null>;
  maskPath: RefObject<SVGPathElement | null>;
  maskId: string;
  active: boolean;
}) {
  return (
    <>
      <svg width="0" height="0" className="hp-mercury-defs" aria-hidden="true" focusable="false">
        <defs>
          <filter
            id={`${maskId}-soft`}
            x="-50%"
            y="-50%"
            width="200%"
            height="200%"
            colorInterpolationFilters="sRGB"
          >
            <feGaussianBlur stdDeviation="0.024 0.018" />
          </filter>
          <mask
            id={maskId}
            maskUnits="objectBoundingBox"
            maskContentUnits="objectBoundingBox"
            x="0"
            y="0"
            width="1"
            height="1"
            style={{ maskType: "alpha" }}
          >
            <path ref={maskPath} fill="white" filter={`url(#${maskId}-soft)`} />
          </mask>
        </defs>
      </svg>
      {createPortal(
        <canvas
          ref={canvas}
          className="hp-mercury-layer"
          aria-hidden="true"
          inert
          hidden={!active}
          data-mercury-state="idle"
        />,
        document.body,
      )}
    </>
  );
}
