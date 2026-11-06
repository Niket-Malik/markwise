export function analysisLabel(aiStatus) {
  if (aiStatus === "complete") return "AI Review Completed";
  if (aiStatus === "retrying") return "AI Analysis Pending/Retrying";
  if (aiStatus === "error" || aiStatus === "not_configured") return "AI Analysis Failed";
  if (aiStatus === "no_sources") return "AI Review Incomplete · No Approved Sources";
  return "Knowledge Base Fallback Review";
}

export function findingsLabel(aiStatus, count) {
  if (aiStatus === "complete") return count ? `${count} finding${count === 1 ? "" : "s"}` : "No conflicts found";
  if (aiStatus === "no_sources") return count ? `${count} findings · source coverage incomplete` : "CANNOT VERIFY";
  if (aiStatus === "retrying") return "AI retrying";
  return count ? `${count} fallback finding${count === 1 ? "" : "s"}` : "Fallback review · incomplete";
}

export function cleanReview(aiStatus, count) {
  return aiStatus === "complete" && count === 0;
}
