/**
 * Format an observation's effective date for display.
 */
export function formatDate(observation: Record<string, any>): string {
  const rawDate =
    observation.effectiveDateTime ?? observation.effectivePeriod?.start;

  if (!rawDate) {
    return "Unknown date";
  }

  const date = new Date(rawDate);

  if (Number.isNaN(date.getTime())) {
    return "Unknown date";
  }

  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
