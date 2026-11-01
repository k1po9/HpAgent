// SPDX-License-Identifier: AGPL-3.0-or-later
// Adapted from rhine-lock frontend/src/lib/ui/PersonnelPanel.svelte at a4df68c. Modified 2026-11
// for HpAgent: real fields and values; the visible name comes from the username field only.
// See web/THIRD_PARTY_NOTICES.md.
import { useState } from "react";
import { CountryFlag, HpMark, type IdentityMode } from "./IdentityChrome";
import type { IdentityPhase } from "./IdentityScene";
import { located, type ClientLocation } from "./useClientLocation";

const SILHOUETTE =
  "M 50 14 C 63 14 71 24 71 38 C 71 50 66 60 59 65 L 60 72 C 72 75 86 80 93 90 C 99 99 101 112 101 124 L -1 124 C -1 112 1 99 7 90 C 14 80 28 75 40 72 L 41 65 C 34 60 29 50 29 38 C 29 24 37 14 50 14 Z";
const FEATURES = [
  ["MAIN", "对话协作"],
  ["WORK", "持续委托"],
  ["RUN", "可追踪执行"],
  ["WORKSPACE", "文件与版本"],
];
const pad = (n: number) => String(n).padStart(2, "0");

function stateLabel(phase: IdentityPhase, rejected: boolean) {
  if (phase === "confirmed") return "CONFIRMED";
  if (phase === "verifying") return "VERIFYING";
  return rejected ? "NOT VERIFIED" : "AWAITING";
}

/** Everything of the personnel file above the form. The h1 stays the accessible page title. */
export function IdentityPanelFile({
  phase,
  mode,
  username,
  capsLock,
  keyEntered,
  rejection,
  denied,
  location,
  title,
}: {
  phase: IdentityPhase;
  mode: IdentityMode;
  username: string;
  capsLock: boolean;
  keyEntered: boolean;
  rejection: number;
  denied: boolean;
  location: ClientLocation;
  title?: string;
}) {
  const [openedAt] = useState(() => new Date());
  const name = username.trim().toUpperCase() || "OPERATOR";
  const rings = [
    { label: "CAPS", on: capsLock, warn: true },
    { label: "NAME", on: !!username, warn: false },
    { label: "KEY", on: keyEntered, warn: false },
  ];
  return (
    <>
      <div className="rear" aria-hidden="true" />
      <div className="glass" aria-hidden="true">
        <div className="hex" />
        <div className="ghost-name">{name}</div>
        <div className="ghost-info">INFO</div>
        <i className="bevel t" />
        <i className="bevel l" />
        <i className="bevel b" />
        <i className="bevel r" />
      </div>
      <div className="tags-r" aria-hidden="true">
        <span className="llc">HPAGENT</span>
      </div>
      <div className="photo" aria-hidden="true">
        <div className="film">
          <svg className="sil" viewBox="0 0 100 124" preserveAspectRatio="none">
            <defs>
              <pattern id="hp-identity-dots" width="3.2" height="3.2" patternUnits="userSpaceOnUse">
                <circle cx="1.6" cy="1.6" r="0.75" fill="#ffffff" fillOpacity="0.16" />
              </pattern>
            </defs>
            <path d={SILHOUETTE} fill="#141516" />
            <path d={SILHOUETTE} fill="url(#hp-identity-dots)" />
          </svg>
          <span className="classified">PHOTO CLASSIFIED</span>
          {phase !== "idle" ? <i className="scan" /> : null}
        </div>
        <div className="badge">
          <HpMark width={24} />
        </div>
        <i className="tick" style={{ right: -3, top: -3 }} />
        <i className="tick" style={{ left: -3, bottom: -3 }} />
        <i className="tick" style={{ right: -3, bottom: -3 }} />
      </div>
      <div className="identity">
        <div className="rank" aria-hidden="true">
          <span className="state" data-alert={denied || undefined}>
            {stateLabel(phase, denied)}
          </span>
        </div>
        <h1 className="name" aria-label={title ?? `HpAgent ${mode === "login" ? "登录" : "注册"}`}>
          <span>{name}</span>
        </h1>
      </div>
      <div className="profile" aria-hidden="true">
        <div className="ph">PROFILE</div>
        <dl>
          <dt>MODE</dt>
          <dd>{mode.toUpperCase()}</dd>
          <dt>OPENED AT</dt>
          <dd>{`${pad(openedAt.getHours())}:${pad(openedAt.getMinutes())}:${pad(openedAt.getSeconds())}`}</dd>
          <dt>IP</dt>
          <dd>{located(location, (ready) => ready.ip)}</dd>
          <dt>REGION</dt>
          <dd>
            <CountryFlag location={location} />
            {located(location, (ready) => ready.region)}
          </dd>
          <dt>ATTEMPT</dt>
          <dd>{pad(rejection + 1)}</dd>
        </dl>
      </div>
      <div className="rings" aria-hidden="true">
        {rings.map((ring) => (
          <div className="ring" key={ring.label}>
            <i data-on={ring.on || undefined} data-warn={(ring.warn && ring.on) || undefined} />
            <span>{ring.label}</span>
          </div>
        ))}
      </div>
      <div className="features" aria-hidden="true">
        {FEATURES.map(([code, text]) => (
          <div key={code}>
            <b>{code}</b>
            <span>{text}</span>
          </div>
        ))}
      </div>
    </>
  );
}
