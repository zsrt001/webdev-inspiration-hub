import { next } from "@vercel/edge";

const SENSITIVE_PREFIXES = [
  "/api/v1/auth",
  "/api/v1/orders",
  "/api/v1/payments",
  "/api/v1/subscriptions",
  "/api/v1/session",
  "/api/v1/upload",
];

const EXEMPT_PREFIXES = ["/health", "/static", "/style-previews"];

function createLimiters() {
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) return null;

  const { Ratelimit } = require("@upstash/ratelimit");
  const { Redis } = require("@upstash/redis");
  const redis = new Redis({ url, token });

  return {
    sensitive: new Ratelimit({
      redis,
      limiter: Ratelimit.slidingWindow(40, "60 s"),
      prefix: "rl:sensitive",
    }),
    default: new Ratelimit({
      redis,
      limiter: Ratelimit.slidingWindow(240, "60 s"),
      prefix: "rl:default",
    }),
  };
}

let limiters: ReturnType<typeof createLimiters> = null;

export default async function middleware(request: Request) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (EXEMPT_PREFIXES.some((p) => path.startsWith(p))) {
    return next();
  }

  if (!path.startsWith("/api/")) {
    return next();
  }

  if (!limiters) {
    limiters = createLimiters();
  }
  if (!limiters) {
    return next();
  }

  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  const isSensitive = SENSITIVE_PREFIXES.some((p) => path.startsWith(p));
  const limiter = isSensitive ? limiters.sensitive : limiters.default;

  try {
    const { success, limit, remaining, reset } = await limiter.limit(ip);

    if (!success) {
      return new Response(JSON.stringify({ error: "rate_limit_exceeded" }), {
        status: 429,
        headers: {
          "Content-Type": "application/json",
          "X-RateLimit-Limit": limit.toString(),
          "X-RateLimit-Remaining": "0",
          "X-RateLimit-Reset": reset.toString(),
          "Retry-After": Math.ceil((reset - Date.now()) / 1000).toString(),
        },
      });
    }
  } catch {
    return next();
  }

  return next();
}

export const config = {
  matcher: ["/api/:path*"],
};
