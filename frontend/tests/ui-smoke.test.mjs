import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("../.next/server/app/index.html", import.meta.url), "utf8");
const cssPath = html.match(/href="([^\"]+\.css)"/)?.[1];
assert.ok(cssPath, "production HTML should reference a stylesheet");
const css = await readFile(new URL(`../.next${cssPath.replace("/_next", "")}`, import.meta.url), "utf8");
const scriptPaths = [...html.matchAll(/src="([^\"]+\.js)"/g)].map((match) => match[1]);
const clientCode = (await Promise.all(scriptPaths.map((path) => readFile(new URL(`../.next${path.replace("/_next", "")}`, import.meta.url), "utf8")))).join("\n");

test("production login screen exposes labelled credentials and responsive viewport", () => {
  assert.match(html, /<main[^>]*class="auth-shell"/);
  assert.match(html, /<label[^>]*>Work email<input type="email"[^>]*name="email"/);
  assert.match(html, /<label[^>]*>Password<input type="password"[^>]*name="password"/);
  assert.match(html, /name="viewport" content="width=device-width, initial-scale=1"/);
});

test("production stylesheet retains keyboard focus and narrow-screen layouts", () => {
  assert.match(css, /:focus-visible[^}]*outline:[^;]+/);
  assert.match(css, /@media\s*\(max-width:\s*700px\)/);
});

test("workspace navigation has an announced landmark, active state, and skip link", () => {
  assert.match(clientCode, /Skip to main content/);
  assert.match(clientCode, /Workspace navigation/);
  assert.match(clientCode, /aria-current/);
  assert.match(css, /\.skip-link:focus\{top:12px\}/);
});

test("keyboard focus indicator has readable contrast against white", () => {
  const focus = css.match(/:focus-visible[^}]*outline:[^#]*#([0-9a-f]{6})/i)?.[1];
  assert.ok(focus, "focus outline should use an explicit six-digit color");
  const channels = focus.match(/.{2}/g).map(value => parseInt(value, 16) / 255);
  const luminance = channels.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  const ratio = 1.05 / (0.2126 * luminance[0] + 0.7152 * luminance[1] + 0.0722 * luminance[2] + 0.05);
  assert.ok(ratio >= 3, `focus indicator contrast ${ratio.toFixed(2)}:1 should meet the 3:1 non-text contrast criterion`);
});
