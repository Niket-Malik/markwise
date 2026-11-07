import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { canApplyFindingSuggestion, isActiveFinding } from "../app/finding-actions.mjs";

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

test("applied and dismissed findings are excluded from the active list after reload", () => {
  assert.equal(isActiveFinding({ id: 1, status: "open" }), true);
  assert.equal(isActiveFinding({ id: 2, status: "edited" }), true);
  for (const status of ["accepted", "dismissed", "applied", "resolved", "stale"]) {
    assert.equal(isActiveFinding({ id: 3, status }), false);
  }
});

test("finding controls use the decision endpoint, prevent duplicate requests, and wait for saves", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /api\(`\/api\/findings\/\$\{id\}\/decision`/);
  assert.match(source, /findingDecisionLocks\.current\.has\(id\)/);
  assert.match(source, /if \(await onDecision\(f\.id, "edited", draftSuggestion, reason\)\) setEditing\(false\)/);
  assert.match(source, /savingAction === "accepted" \? "Applying…"/);
  assert.match(source, /savingAction === "dismissed" \? "Dismissing…"/);
  assert.match(source, /await refresh\(\)/);
  assert.match(source, /setSelected\(current => current \? \{ \.\.\.current, findings: current\.findings\.filter\(finding => finding\.id !== id\) \} : current\)/);
  assert.match(source, /const activeFindings = selected\?\.findings\.filter\(isActiveFinding\) \|\| \[\]/);
});

test("edited suggestions can be saved without a dismissal reason and stay open on failed saves", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /disabled=\{saving \|\| !draftSuggestion\.trim\(\)\}/);
  assert.match(source, /if \(await onDecision\(f\.id, "edited", draftSuggestion, reason\)\) setEditing\(false\)/);
  assert.match(source, /savingAction === "edited" \? "Saving…" : "Save Edited Suggestion"/);
  assert.match(source, /catch \(e\) \{\s*setError\(\(e as Error\)\.message\);\s*return false;/);
});

test("finding history is fetched from persisted backend decisions and renders evidence fields", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /api\(`\/api\/reviews\/\$\{selected\.id\}\/finding-history`\)/);
  assert.match(source, /api\(`\/api\/reviews\/\$\{refreshedReview\.id\}\/finding-history`\)/);
  assert.match(source, /Finding History/);
  assert.match(source, /entry\.excerpt/);
  assert.match(source, /entry\.reason/);
  assert.match(source, /entry\.timestamp/);
  assert.match(source, /entry\.applied_rewrite/);
});
