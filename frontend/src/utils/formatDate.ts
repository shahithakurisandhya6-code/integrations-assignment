/**
 * Format an observation's effective date for display.
 */
export function formatDate(observation: Record<string, any>): string {
  const date = new Date(observation.effectiveDateTime);
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
