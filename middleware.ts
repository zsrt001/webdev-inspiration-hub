declare const process: {
  env: Record<string, string | undefined>;
};

const SENSITIVE_PREFIXES = [
  "/api/v1/auth",
  "/api/v1/orders",
  "/api/v1/payments",
  "/api/v1/subscriptions",
  "/api/v1/session",
  "/api/v1/media",
];

const EXEMPT_PREFIXES = ["/health", "/static", "/style-previews"];

type LimitConfig = {
  limit: number;
  windowSeconds: number;
  prefix: string;
};

const SECURITY_HEADERS: Record<string, string> = {
  "Content-Security-Policy": "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; connect-src 'self' https://*.supabase.co; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; upgrade-insecure-requests",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
};

const DEFAULT_LIMIT: LimitConfig = {
  limit: 240,
  windowSeconds: 60,
  prefix: "rl:default",
};

const SENSITIVE_LIMIT: LimitConfig = {
  limit: 40,
  windowSeconds: 60,
  prefix: "rl:sensitive",
};

function continueRequest(): Response {
  return new Response(null, {
    headers: {
      "x-middleware-next": "1",
      ...SECURITY_HEADERS,
    },
  });
}

function upstashConfig(): { url: string; token: string } | null {
  const url = process.env.UPSTASH_REDIS_REST_URL?.replace(/\/+$/, "");
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;
  return { url, token };
}

async function checkLimit(ip: string, config: LimitConfig) {
  const redis = upstashConfig();
  if (!redis) return { allowed: true };

  const now = Date.now();
  const cutoff = now - config.windowSeconds * 1000;
  const key = `${config.prefix}:${ip}`;
  const member = `${now}:${crypto.randomUUID()}`;

  const response = await fetch(`${redis.url}/pipeline`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${redis.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify([
      ["ZREMRANGEBYSCORE", key, "-inf", cutoff],
      ["ZADD", key, now, member],
      ["ZCARD", key],
      ["EXPIRE", key, config.windowSeconds],
    ]),
  });

  if (!response.ok) return { allowed: true };
  const results = (await response.json()) as Array<{ result?: unknown }>;
  const count = Number(results?.[2]?.result || 0);
  const reset = now + config.windowSeconds * 1000;

  return {
    allowed: count <= config.limit,
    limit: config.limit,
    remaining: Math.max(config.limit - count, 0),
    reset,
  };
}

export default async function middleware(request: Request) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (EXEMPT_PREFIXES.some((prefix) => path.startsWith(prefix))) {
    return continueRequest();
  }

  if (!path.startsWith("/api/")) {
    return continueRequest();
  }

  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  const isSensitive = SENSITIVE_PREFIXES.some((prefix) => path.startsWith(prefix));

  try {
    const result = await checkLimit(ip, isSensitive ? SENSITIVE_LIMIT : DEFAULT_LIMIT);
    if (!result.allowed) {
      const requestId = crypto.randomUUID();
      return new Response(JSON.stringify({
        code: "rate_limited",
        message: "Too many requests. Please wait a moment.",
        request_id: requestId,
        retryable: true,
        field_errors: [],
      }), {
        status: 429,
        headers: {
          "Content-Type": "application/json",
          ...SECURITY_HEADERS,
          "X-Request-ID": requestId,
          "X-RateLimit-Limit": String(result.limit),
          "X-RateLimit-Remaining": String(result.remaining),
          "X-RateLimit-Reset": String(result.reset),
          "Retry-After": String(Math.ceil(((result.reset || Date.now()) - Date.now()) / 1000)),
        },
      });
    }
  } catch {
    return continueRequest();
  }

  return continueRequest();
}

export const config = {
  matcher: ["/api/:path*"],
};
