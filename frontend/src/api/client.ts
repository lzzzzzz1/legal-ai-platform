/** Shared HTTP boundary: authentication headers and safe API error decoding. */

export type ApiErrorPayload = { detail?: string } | null;

export function apiHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "X-Tenant-ID": import.meta.env.VITE_TENANT_ID || "local",
  };
  const apiToken = import.meta.env.VITE_API_AUTH_TOKEN;
  if (apiToken) headers["X-API-Token"] = apiToken;
  return headers;
}

export async function getApiErrorMessage(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null) as ApiErrorPayload;
  return payload?.detail || fallback;
}
