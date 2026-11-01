const sourceUrl = import.meta.env.VITE_HPAGENT_SOURCE_URL as string | undefined;

/** AGPL-3.0 §13 notice. Deployments set the source location at build time. */
export function LicenseNotice() {
  return (
    <p className="hp-license-notice">
      HpAgent 以 AGPL-3.0-or-later 授权
      {sourceUrl ? (
        <>
          {" · "}
          <a href={sourceUrl} target="_blank" rel="noreferrer">
            源代码
          </a>
        </>
      ) : null}
    </p>
  );
}
