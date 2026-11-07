const GENERIC_CANNOT_VERIFY_SUGGESTIONS = new Set([
  "confirm this claim with an approved source before publication.",
  "confirm this statement with an approved source before publication.",
  "confirm this figure with an approved source before publication.",
]);

export function canApplyFindingSuggestion(finding, content, channel) {
  if (channel === "Image" || !finding.suggestion?.trim()) return false;
  if (!(finding.kind === "claim" || finding.kind === "cannot_verify" || finding.kind.startsWith("ai_"))) return false;
  if (finding.kind === "cannot_verify" && GENERIC_CANNOT_VERIFY_SUGGESTIONS.has(finding.suggestion.trim().toLowerCase())) return false;
  return content.includes(finding.excerpt) && content.indexOf(finding.excerpt) === content.lastIndexOf(finding.excerpt);
}

export function isActiveFinding(finding) {
  return !["stale", "accepted", "dismissed", "applied", "resolved"].includes(finding.status);
}
