import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

test("workspace edit and destructive controls are admin-only and use workspace endpoints", () => {
  assert.match(page, /user\.role === "admin" && <section className="panel privacy-panel">[\s\S]*?Workspace details/);
  assert.match(page, /api\("\/api\/settings\/organization", \{ method: "PATCH"/);
  assert.match(page, /api\("\/api\/settings\/organization", \{ method: "DELETE"/);
});

test("team member edit and remove controls render only for admins", () => {
  assert.match(page, /isAdmin=\{user\.role === "admin"\} onSave=\{saveTeamMember\} onRemove=\{removeTeamMember\}/);
  assert.match(page, /api\(`\/api\/team\/\$\{memberId\}`, \{ method: "PATCH"/);
  assert.match(page, /api\(`\/api\/team\/\$\{memberId\}`, \{ method: "DELETE"/);
});

test("knowledge edits and deletes are admin-only and review deletion is limited to decided reviews", () => {
  assert.match(page, /isAdmin=\{user\.role === "admin"\} onApprove=\{approveKnowledge\} onSave=\{saveKnowledgeItem\} onDelete=\{deleteKnowledgeItem\}/);
  assert.match(page, /api\(`\/api\/knowledge\/\$\{itemId\}`, \{ method: "PATCH"/);
  assert.match(page, /api\(`\/api\/knowledge\/\$\{itemId\}`, \{ method: "DELETE"/);
  assert.match(page, /canDelete=\{user\.role === "admin"\} onDelete=\{deleteReview\}/);
  assert.match(page, /\["approved", "rejected"\]\.includes\(review\.status\)/);
});
