// SPDX-License-Identifier: AGPL-3.0-or-later
// Layout and styling adapted from rhine-lock frontend/src/lib/ui/ at a4df68c: Header, LeftRail,
// SectionTile, MediaCard, Callouts, Clock, Subtitle, AuthRing and Warning. Modified 2026-11 for
// HpAgent: its own wordmark and only facts this page really knows; no media, power controls or
// measured-looking progress. See web/THIRD_PARTY_NOTICES.md.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { readClientInfo, type ClientInfo } from "./clientInfo";
import { DEFAULT_ANCHORS } from "./identityAnchors";
import type { IdentityPhase } from "./IdentityScene";
import { located, type ClientLocation } from "./useClientLocation";

export type IdentityMode = "login" | "register";

const DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(" ");
const pad = (n: number) => String(n).padStart(2, "0");

export function HpMark({ width, color = "currentColor" }: { width: number; color?: string }) {
  return (
    <svg className="hp-identity-mark" width={width} viewBox="0 0 40 24" aria-hidden="true">
      <text x="20" y="19" textAnchor="middle" fill={color}>
        Hp
      </text>
    </svg>
  );
}

/** Hides itself on any failure: a missing flag never leaves a broken image behind. */
export function CountryFlag({ location }: { location: ClientLocation }) {
  const code = location.status === "ready" ? location.countryCode : null;
  const [failedCode, setFailedCode] = useState<string | null>(null);
  if (!code || failedCode === code) return null;
  return (
    <img
      className="hp-identity-flag"
      src={`https://cdn.ipwhois.io/flags/${code}.svg`}
      alt=""
      referrerPolicy="no-referrer"
      onError={() => setFailedCode(code)}
    />
  );
}

export function IdentityHeader() {
  const line1 = useRef<HTMLDivElement>(null);
  const line2 = useRef<HTMLDivElement>(null);
  const line3 = useRef<HTMLDivElement>(null);
  // Every line spans the first: by tracking on line 2, by size on line 3.
  useLayoutEffect(() => {
    let live = true;
    const measure = () => {
      if (!live || !line1.current || !line2.current || !line3.current) return;
      const width = line1.current.offsetWidth;
      if (!width) return;
      const two = line2.current;
      two.style.letterSpacing = "0px";
      two.style.letterSpacing = `${(width - two.scrollWidth) / ((two.textContent?.length ?? 2) - 1)}px`;
      const three = line3.current;
      three.style.fontSize = "40px";
      three.style.fontSize = `${(40 * width) / three.scrollWidth}px`;
    };
    measure();
    void document.fonts?.ready.then(measure);
    return () => {
      live = false;
    };
  }, []);
  return (
    <div className="hp-identity-header" aria-label="HpAgent 身份终端" role="img">
      <div className="l1" ref={line1}>
        HPAGENT
      </div>
      <div className="l2" ref={line2}>
        SYNTHESIZE INFORMATION
      </div>
      <div className="l3" ref={line3}>
        <span>IDENTITY</span>
        <b>TERMINAL</b>
      </div>
    </div>
  );
}

function SectionTile({
  caption,
  tab,
  width = 376,
  height = 263,
}: {
  caption?: ReactNode;
  tab?: string;
  width?: number;
  height?: number;
}) {
  return (
    <div className="hp-identity-tile" style={{ width, height }}>
      {tab ? <div className="tab">{tab}</div> : null}
      <div className="plate">
        <div className="smoke" />
        <i className="reg" />
        <div className="code" style={{ fontSize: Math.round(height * 0.4) }}>
          HP
        </div>
        {caption ? <div className="cap">{caption}</div> : null}
      </div>
    </div>
  );
}

function logRows(phase: IdentityPhase, rejection: number) {
  const rows: string[] = [];
  if (rejection) rows.push(`LAST REQUEST NOT VERIFIED · ${pad(rejection)}`);
  if (phase === "verifying") rows.push("REQUEST SUBMITTED · VERIFYING");
  if (phase === "confirmed") rows.push("IDENTITY CONFIRMED");
  return rows.length ? rows : ["AWAITING REQUEST"];
}

function markerY(phase: IdentityPhase, denied: boolean) {
  if (denied) return 700;
  return phase === "idle" ? 347 : 520;
}

function LeftRail({
  phase,
  denied,
  rejection,
  mode,
  location,
  client,
}: {
  phase: IdentityPhase;
  denied: boolean;
  rejection: number;
  mode: IdentityMode;
  location: ClientLocation;
  client: ClientInfo;
}) {
  const rows = logRows(phase, rejection);
  return (
    <div className="hp-identity-rail">
      <div className="rule" />
      <div className="marker" style={{ top: markerY(phase, denied) }} />
      <i className="bar" />
      <i className="dash" style={{ left: 46, top: 368 }} />
      <div className="chip" style={{ left: 46, top: 404 }}>
        TERMINAL DATA
      </div>
      <dl className="micro">
        <dt>LANG</dt>
        <dd>{client.language}</dd>
        <dt>TIMEZONE</dt>
        <dd>{client.timezone}</dd>
        <dt>SCREEN</dt>
        <dd>{client.screen}</dd>
        <dt>NETWORK</dt>
        <dd>{located(location, (ready) => ready.isp)}</dd>
      </dl>
      <div className="chip" style={{ left: 46, top: 640 }}>
        COMMAND ENTERED
      </div>
      <div className="log">
        {rows.map((row, i) => (
          <div key={row} style={{ opacity: 0.35 + (0.65 * (i + 1)) / rows.length }}>
            <i />
            {row}
          </div>
        ))}
      </div>
      <i className="dash" style={{ left: 46, top: 862 }} />
      <i className="hair" />
      <div className="hints">
        <span>
          <b>ENTER</b>SUBMIT
        </span>
        <span>
          <b>TAB</b>NEXT FIELD
        </span>
      </div>
      <i className="foot" />
      <div className="label">
        {denied ? "IDENTITY SECTION · NOT VERIFIED" : `${mode.toUpperCase()} · IDENTITY SECTION`}
      </div>
    </div>
  );
}

function AccessFeed({ phase, denied }: { phase: IdentityPhase; denied: boolean }) {
  const status = denied
    ? "NOT VERIFIED"
    : phase === "idle"
      ? "AWAITING CREDENTIALS"
      : phase === "verifying"
        ? "VERIFYING"
        : "IDENTITY CONFIRMED";
  return (
    <div className="hp-identity-feed" data-live={phase !== "idle" || undefined}>
      <div className="head">
        <span>ENTRY CHANNELS</span>
        <em />
        <i />
      </div>
      <div className="body">
        <div className="art">
          <HpMark width={46} color="#fff" />
        </div>
        <div className="meta">
          <b>WEB / QQ</b>
          <span>绑定 QQ 后共用账户与长期记忆</span>
          <div className="state">{status}</div>
        </div>
      </div>
    </div>
  );
}

function Clock({
  phase,
  typed,
  denied,
  client,
}: {
  phase: IdentityPhase;
  typed: boolean;
  denied: boolean;
  client: ClientInfo;
}) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  const step = denied || phase === "confirmed" ? 4 : phase === "verifying" ? 3 : typed ? 2 : 1;
  return (
    <div className="hp-identity-clock">
      <div className="steps">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="node" data-on={i === step || undefined}>
            <i className="dot" />
            {i === step ? (
              <>
                <i className="line" />
                <i className="ring" />
              </>
            ) : null}
          </div>
        ))}
      </div>
      <div className="time">
        <span className="hm">{`${pad(now.getHours())}:${pad(now.getMinutes())}`}</span>
        <span className="sec">{pad(now.getSeconds())}</span>
      </div>
      <div className="date">
        {`${DAYS[now.getDay()]}  ·  ${pad(now.getDate())} ${MONTHS[now.getMonth()]} ${now.getFullYear()}`}
      </div>
      <div className="zone">
        <i />
        {client.timezone} · {client.utcOffset}
      </div>
    </div>
  );
}

function subtitle(phase: IdentityPhase, denied: boolean, mode: IdentityMode): [string, string] {
  if (denied) return ["本次验证未完成，详情见身份面板。", "THE REQUEST WAS NOT VERIFIED."];
  if (phase === "verifying") return ["正在核对身份凭证……", "CROSS-CHECKING YOUR CREDENTIALS."];
  if (phase === "confirmed") return ["身份已确认，正在进入工作空间。", "IDENTITY CONFIRMED."];
  return mode === "login"
    ? ["请出示身份凭证以进入 HpAgent。", "PRESENT YOUR CREDENTIALS TO ENTER HPAGENT."]
    : ["请填写信息以创建 HpAgent 账户。", "COMPLETE THE FILE TO CREATE YOUR ACCOUNT."];
}

function arc(r: number, start: number, sweep: number) {
  const point = (angle: number) =>
    `${200 + r * Math.cos((angle * Math.PI) / 180)} ${200 + r * Math.sin((angle * Math.PI) / 180)}`;
  return `M ${point(start)} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${point(start + sweep)}`;
}

function AuthRing({ active, label }: { active: boolean; label: string }) {
  return (
    <div className="hp-identity-ring" data-active={active || undefined}>
      <div className="halo" />
      <svg viewBox="0 0 400 400" width="400" height="400">
        <g className="r1">
          <path d={arc(150, -60, 300)} />
        </g>
        <g className="r2">
          <path d={arc(118, 200, 70)} className="thick" />
        </g>
        <g className="r3">
          <path d={arc(52, 10, 120)} />
          <path d={arc(52, 190, 120)} />
        </g>
        <g className="o1">
          <circle cx="350" cy="200" r="4.5" />
          <circle cx="50" cy="200" r="3" />
        </g>
        <g className="o2">
          <circle cx="200" cy="108" r="3.5" />
        </g>
      </svg>
      <div className="label">{label}</div>
    </div>
  );
}

function Warning({ attempt }: { attempt: number }) {
  // A rejection can also be a network failure, so nothing here alleges an intruder.
  const rows = [
    "REQUEST NOT VERIFIED",
    `ATTEMPT ${pad(attempt)}`,
    "DETAILS ON THE IDENTITY PANEL",
    "RETURNING TO ACCESS PORTAL",
  ];
  return (
    <div className="hp-identity-warning">
      <div className="halo" />
      <div className="box">
        <svg className="tri" width="30" height="27" viewBox="0 0 30 27">
          <path d="M15 1 L29 26 L1 26 Z" />
          <rect x="14" y="9" width="2" height="9" />
          <rect x="14" y="20" width="2" height="2" />
        </svg>
        <div className="plate">
          <i />
          WARNING
          <i />
        </div>
        {rows.map((row, i) => (
          <div
            key={`${attempt}-${row}`}
            className="row"
            data-last={i === 3 || undefined}
            style={{ animationDelay: `${260 + i * 240}ms` }}
          >
            <span>{row}</span>
            <b />
          </div>
        ))}
      </div>
    </div>
  );
}

function sessionDetail(phase: IdentityPhase) {
  if (phase === "confirmed") return "SESSION ESTABLISHED";
  return phase === "verifying" ? "VERIFYING · NO SESSION YET" : "NO ACTIVE SESSION";
}

/** The decorative design-canvas layer. Hidden from assistive tech: the form says all of this. */
export function IdentityChrome({
  phase,
  denied,
  rejection,
  mode,
  typed,
  location,
}: {
  phase: IdentityPhase;
  denied: boolean;
  rejection: number;
  mode: IdentityMode;
  typed: boolean;
  location: ClientLocation;
}) {
  const [client] = useState(readClientInfo);
  const [zh, en] = subtitle(phase, denied, mode);
  const { a, b } = DEFAULT_ANCHORS;
  return (
    <div className="hp-identity-chrome" aria-hidden="true">
      <LeftRail
        phase={phase}
        denied={denied}
        rejection={rejection}
        mode={mode}
        location={location}
        client={client}
      />
      <div className="hp-identity-tile-at">
        <SectionTile
          tab={mode.toUpperCase()}
          caption={
            <>
              <CountryFlag location={location} />
              {located(location, (ready) => ready.region)}
            </>
          }
        />
      </div>
      <div className="hp-identity-feed-at">
        <AccessFeed phase={phase} denied={denied} />
      </div>
      <div className="hp-identity-callouts">
        <svg width="1920" height="1080">
          <line data-anchor-line="a" x1={a.x} y1={a.y} x2={732} y2={126} />
          <line data-anchor-line="b" x1={b.x} y1={b.y} x2={710} y2={882} />
          <circle data-anchor-dot="a" cx={a.x} cy={a.y} r="3.5" />
          <circle data-anchor-dot="b" cx={b.x} cy={b.y} r="3.5" />
        </svg>
        <div className="label" style={{ left: 742, top: 96 }}>
          <span>SERVICES SECT</span>
          <b>GEO POSITION</b>
          <em>
            <CountryFlag location={location} />
            {located(location, (ready) => (ready.position ? `≈ ${ready.position}` : null))}
          </em>
        </div>
        <div className="label" style={{ left: 720, top: 852 }}>
          <span>SERVICES SECT</span>
          <b>CLIENT</b>
          <em>
            {client.browser} · {client.system} · {client.screen} · {sessionDetail(phase)}
          </em>
        </div>
      </div>
      <div className="hp-identity-clock-at">
        <Clock phase={phase} typed={typed} denied={denied} client={client} />
      </div>
      <div className="hp-identity-subtitle" key={zh}>
        <div className="zh">{zh}</div>
        <div className="en">{en}</div>
      </div>
      <div className="hp-identity-anchor">
        <AuthRing
          active={phase !== "idle"}
          label={phase === "confirmed" ? "IDENTITY CONFIRMED" : "VERIFYING IDENTITY"}
        />
        {denied ? <Warning attempt={rejection} /> : null}
      </div>
    </div>
  );
}
