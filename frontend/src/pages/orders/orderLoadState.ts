export interface OrderLoadFailureState {
  authRequired: boolean;
  message: string;
}

interface HttpFailureLike {
  statusCode?: unknown;
  message?: unknown;
}

export function resolveOrderLoadFailure(
  error: unknown,
  fallbackMessage: string,
): OrderLoadFailureState {
  const failure = error && typeof error === 'object'
    ? error as HttpFailureLike
    : {};
  const statusCode = Number(failure.statusCode || 0);

  if (statusCode === 401 || statusCode === 403) {
    return { authRequired: true, message: '' };
  }

  const message = typeof failure.message === 'string' && failure.message.trim()
    ? failure.message
    : fallbackMessage;
  return { authRequired: false, message };
}
