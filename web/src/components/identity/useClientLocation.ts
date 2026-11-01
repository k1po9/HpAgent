import { useEffect, useState } from "react";

// A third-party lookup, chosen by the operator: the visitor's IP reaches this service.
const LOOKUP_URL =
  "https://ipwho.is/?lang=zh-CN&fields=success,ip,country,country_code,region,city,latitude,longitude,connection.isp";

export interface LocatedClient {
  status: "ready";
  ip: string;
  /** Country, region and city, each present only if the service gave it. */
  region: string;
  /** Lower-case ISO 3166-1 alpha-2, or null. */
  countryCode: string | null;
  /** Approximate, city-level position derived from the IP. */
  position: string | null;
  isp: string | null;
}
export type ClientLocation = { status: "unavailable" } | LocatedClient;

const text = (value: unknown) => (typeof value === "string" && value ? value : null);

function formatPosition(latitude: unknown, longitude: unknown) {
  if (typeof latitude !== "number" || typeof longitude !== "number") return null;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
  const northSouth = latitude < 0 ? "S" : "N";
  const eastWest = longitude < 0 ? "W" : "E";
  return `${Math.abs(latitude).toFixed(2)}°${northSouth} ${Math.abs(longitude).toFixed(2)}°${eastWest}`;
}

export function parseLocation(body: unknown): ClientLocation {
  if (typeof body !== "object" || body === null) return { status: "unavailable" };
  const fields = body as Record<string, unknown>;
  const ip = text(fields.ip);
  if (fields.success !== true || !ip) return { status: "unavailable" };
  const places = [fields.country, fields.region, fields.city]
    .map(text)
    .filter((place): place is string => !!place);
  const code = text(fields.country_code);
  const connection = fields.connection as Record<string, unknown> | null | undefined;
  return {
    status: "ready",
    ip,
    region: [...new Set(places)].join(" "),
    // This code goes into an image URL, so only the exact shape is accepted.
    countryCode: code && /^[A-Za-z]{2}$/.test(code) ? code.toLowerCase() : null,
    position: formatPosition(fields.latitude, fields.longitude),
    isp: text(connection?.isp),
  };
}

/**
 * Starts unavailable and updates as soon as the browser lookup returns valid data.
 * No application timeout or retry; failures stay unavailable. Never blocks the form.
 * The result stays in memory and the request is cancelled on cleanup.
 */
export function useClientLocation(enabled: boolean): ClientLocation {
  const [location, setLocation] = useState<ClientLocation>({ status: "unavailable" });
  useEffect(() => {
    if (!enabled) return;
    let unmounted = false;
    const controller = new AbortController();
    fetch(LOOKUP_URL, {
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((body: unknown) => {
        if (!unmounted) setLocation(parseLocation(body));
      })
      .catch(() => {
        if (!unmounted) setLocation({ status: "unavailable" });
      });
    return () => {
      unmounted = true;
      controller.abort();
    };
  }, [enabled]);
  return location;
}

/** The shared display rule for any field that depends on the lookup. */
export function located(
  location: ClientLocation,
  read: (ready: LocatedClient) => string | null,
): string {
  if (location.status === "unavailable") return "UNAVAILABLE";
  return read(location) || "UNAVAILABLE";
}
