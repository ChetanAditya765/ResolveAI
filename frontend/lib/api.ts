export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    const timeout = AbortSignal.timeout(25000);
    const signal = options.signal
      ? AbortSignal.any([options.signal, timeout])
      : timeout;
    response = await fetch(`/api${path}`, {
      ...options,
      headers,
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(
      "The service could not be reached. Please retry.",
      0,
      "connection_failed",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      body?.error?.message ?? "The request could not be completed.",
      response.status,
      body?.error?.code ?? "request_failed",
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please retry.";
}
