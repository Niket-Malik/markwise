import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { canApplyFindingSuggestion } from "../app/finding-actions.mjs";

test("contextual CANNOT VERIFY rewrite can be applied but generic advice cannot", () => {
  const finding = {
    kind: "cannot_verify",
    excerpt: "Our service doubles your sales",
    suggestion: "Explore how our service could support your team's workflow.",
  };
  assert.equal(canApplyFindingSuggestion(finding, finding.excerpt, "Website"), true);
  assert.equal(canApplyFindingSuggestion({ ...finding, suggestion: "Confirm this claim with an approved source before publication." }, finding.excerpt, "Website"), false);
  assert.equal(canApplyFindingSuggestion(finding, `${finding.excerpt}. ${finding.excerpt}`, "Website"), false);
  assert.equal(canApplyFindingSuggestion(finding, finding.excerpt, "Image"), false);
});

test("finding controls use the decision endpoint, prevent duplicate requests, and wait for saves", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /api\(`\/api\/findings\/\$\{id\}\/decision`/);
  assert.match(source, /findingDecisionLocks\.current\.has\(id\)/);
  assert.match(source, /if \(await onDecision\(f\.id, "edited", draftSuggestion, reason\)\) setEditing\(false\)/);
  assert.match(source, /savingAction === "accepted" \? "Applying…"/);
  assert.match(source, /savingAction === "dismissed" \? "Dismissing…"/);
  assert.match(source, /await refresh\(\)/);
});
