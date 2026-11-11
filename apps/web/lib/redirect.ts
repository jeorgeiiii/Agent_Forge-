/** Only same-site paths: "/editor/3" is fine, "//evil.com" or "https://evil.com" is not (no open redirect). */
export function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") ? value : "/";
}
