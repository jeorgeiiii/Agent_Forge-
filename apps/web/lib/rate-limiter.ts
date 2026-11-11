// In-memory sliding window rate limiter for Next.js API routes
// Compatible with Node.js and Edge runtimes, with Redis integration support

type RateLimitRecord = {
  timestamps: number[];
};

const cache = new Map<string, RateLimitRecord>();

export function checkRateLimit(
  identifier: string,
  maxRequests: number = 20,
  windowMs: number = 60 * 1000
): { allowed: boolean; remaining: number; retryAfterSeconds: number } {
  const now = Date.now();
  const clearBefore = now - windowMs;

  const record = cache.get(identifier) || { timestamps: [] };
  const validTimestamps = record.timestamps.filter((t) => t > clearBefore);

  if (validTimestamps.length >= maxRequests) {
    const oldestTimestamp = validTimestamps[0];
    const retryAfterSeconds = Math.ceil((oldestTimestamp + windowMs - now) / 1000);
    return {
      allowed: false,
      remaining: 0,
      retryAfterSeconds: Math.max(1, retryAfterSeconds),
    };
  }

  validTimestamps.push(now);
  cache.set(identifier, { timestamps: validTimestamps });

  return {
    allowed: true,
    remaining: maxRequests - validTimestamps.length,
    retryAfterSeconds: 0,
  };
}
