/**
 * API access.
 *
 * Browser calls go through the /api proxy declared in next.config.ts so the session
 * cookie is first-party. Server components call the API directly, which avoids a round
 * trip through the browser and keeps the internal URL out of the client bundle.
 */

export const serverApiBase = process.env.API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

async function toError(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => null);
  const detail = payload?.detail;
  const message =
    (typeof detail === "string" ? detail : detail?.message) ??
    `Request failed (${response.status})`;
  return new ApiError(message, response.status, detail?.code);
}

/** Browser-side call. Always sends the session cookie. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, credentials: "include" });
  if (!response.ok) throw await toError(response);
  return response.json() as Promise<T>;
}

/** Server-side read. Returns null when the resource is absent or the API is unreachable. */
export async function readFromApi<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${serverApiBase}${path}`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    // The donor pages must still render when the API is down; callers show a notice.
    return null;
  }
}
