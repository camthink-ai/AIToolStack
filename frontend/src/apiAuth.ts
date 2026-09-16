/**
 * API authentication support.
 *
 * When the backend runs with API_AUTH_ENABLED=true, every /api and /ws
 * request must present the configured key. This module:
 *  - stores the key in localStorage
 *  - patches window.fetch to add the X-API-Key header to API calls
 *  - raises AUTH_REQUIRED_EVENT when the backend answers 401/auth_required
 *    (the ApiAuthGate component listens for it and prompts for the key)
 *  - provides withApiAuth() for contexts that cannot send headers
 *    (<img> tags, WebSocket URLs) — those append ?api_key= instead
 */

const STORAGE_KEY = 'aitoolstack_api_key';

export const AUTH_REQUIRED_EVENT = 'aitoolstack:auth-required';

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setApiKey(key: string): void {
  try {
    const trimmed = (key || '').trim();
    if (trimmed) {
      localStorage.setItem(STORAGE_KEY, trimmed);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Storage unavailable (private mode etc.) — nothing we can do
  }
}

/** Append the api_key query parameter for header-less contexts.
 * Non-API URLs and data: URLs are returned untouched. */
export function withApiAuth(url: string): string {
  const key = getApiKey();
  if (!key || !url) return url;
  if (url.startsWith('data:') || url.startsWith('blob:')) return url;
  // Only same-app endpoints carry the key; never leak it to 3rd-party hosts
  if (!/\/api(\/|$)|\/ws(\/|$)/.test(url)) return url;
  try {
    const u = new URL(url, window.location.origin);
    if (!u.searchParams.has('api_key')) {
      u.searchParams.set('api_key', key);
    }
    return u.toString();
  } catch {
    return url;
  }
}

/** Install once at app bootstrap, before any component fetches data. */
export function installApiAuthInterceptor(): void {
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> => {
    const key = getApiKey();
    let nextInput: RequestInfo | URL = input;
    let nextInit: RequestInit | undefined = init;

    if (key) {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
          ? input.toString()
          : input.url;
      if (/\/api(\/|$)|\/ws(\/|$)/.test(url)) {
        const headers = new Headers(
          init?.headers || (typeof input === 'object' && !(input instanceof URL) ? input.headers : undefined)
        );
        if (!headers.has('X-API-Key')) {
          headers.set('X-API-Key', key);
          nextInit = { ...(init || {}), headers };
        }
      }
    }

    const response = await originalFetch(nextInput, nextInit);

    if (response.status === 401) {
      try {
        const data = await response.clone().json();
        if (data && data.code === 'auth_required') {
          window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
        }
      } catch {
        // Not a JSON 401 — ignore
      }
    }

    return response;
  };
}
