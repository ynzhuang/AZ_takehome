// UI smoke test against the running app (optional; needs Playwright: `npm i -D playwright && npx playwright install chromium`).
//
//   APP_URL     app with a working LLM backend      (default http://localhost:5173)
//   BROKEN_URL  app whose backend has an unreachable LLM endpoint, to exercise the error state (optional)
//   SHOTS       directory for screenshots            (default ../docs/screenshots)
//
// Prints PASS/FAIL per check and exits non-zero on any failure.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

// require() so that either a local or a globally installed Playwright is found
const { chromium } = createRequire(import.meta.url)("playwright");

const APP = process.env.APP_URL ?? "http://localhost:5173";
const BROKEN = process.env.BROKEN_URL;
const SHOTS = process.env.SHOTS ?? new URL("../../docs/screenshots/", import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });

let failures = 0;
const check = (name, ok, extra = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? `  (${extra})` : ""}`);
  if (!ok) failures++;
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2 });
const lastCard = () => page.locator("article.turn").last();
const askAndWait = async (q) => {
  const n = await page.locator("article.turn").count();
  await page.getByLabel("Question").fill(q);
  await page.getByLabel("Question").press("Enter");
  await page.locator("article.turn").nth(n).locator(".badge").waitFor({ timeout: 60_000 });
  return page.locator("article.turn").nth(n);
};

// 1. Startup: summary is on screen before any question
await page.goto(APP);
// Screenshots only: keep the sticky composer from overlapping element screenshots.
await page.addStyleTag({ content: ".composer { position: static; }" });
await page.getByText("net spend ·").waitFor({ timeout: 15_000 });
check("summary loads on startup", await page.getByText("EUR 6,886,631.10").isVisible());
check("interpreter label shown", (await page.locator(".interpreter").innerText()).includes("Figures are calculated by code"));
await page.screenshot({ path: `${SHOTS}01-startup.png` });

// 2. Loading state, then an answered response with evidence collapsed
await page.getByRole("button", { name: "What are our top 5 vendors by total spend in 2025?" }).click();
check("loading state visible", await page.getByText("Interpreting the question").isVisible());
check("Ask disabled while loading", await page.getByRole("button", { name: "Working…" }).isDisabled());
await lastCard().locator(".badge").waitFor({ timeout: 60_000 });
let card = lastCard();
check("T1 answered", (await card.locator(".badge").innerText()) === "Answered");
check("T1 key finding first", (await card.locator(".answer").innerText()).startsWith("Top 5 vendors by net spend"));
check("T1 limitations shown", await card.getByText("Limitations: what this answer cannot tell you").isVisible());
const rowsDetails = card.locator("details").filter({ hasText: "Invoice rows used (85" });
check("T1 evidence collapsed by default", (await rowsDetails.getAttribute("open")) === null);
await rowsDetails.locator("summary").click();
check("T1 85 evidence rows", (await rowsDetails.locator("tbody tr").count()) === 85);
await card.screenshot({ path: `${SHOTS}02-answered-top5.png` });

// 3. Clarification -> choose an option -> answered
card = await askAndWait("What is our consulting spend?");
check("T5 clarification", (await card.locator(".badge").innerText()) === "Needs clarification");
const options = card.locator("button.option");
check("T5 has >= 2 options", (await options.count()) >= 2);
await card.screenshot({ path: `${SHOTS}03-clarification.png` });
const n = await page.locator("article.turn").count();
await options.filter({ hasText: /categor/i }).first().click();
await page.locator("article.turn").nth(n).locator(".badge").waitFor({ timeout: 60_000 });
card = lastCard();
check("T5 option answered", (await card.locator(".badge").innerText()) === "Answered");
check("T5 option filter = Consulting category", await card.locator("code.chip", { hasText: "categories = Consulting" }).isVisible());

// 4. Invoice check: authority cannot be determined
card = await askAndWait("Was INV-20008 approved by someone with sufficient authority?");
const checksText = await card.locator("table.checks").innerText();
const authorityRow = await card.locator("table.checks tr", { hasText: "Approver has authority" }).innerText();
check("T6 authority = cannot determine", authorityRow.includes("Cannot determine from available data"));
check("T6 wording does not overclaim", checksText.includes("No issue detected in checked fields") && !/compliant/i.test(checksText));
await card.screenshot({ path: `${SHOTS}04-invoice-check.png` });

// 5. Declined
card = await askAndWait("What was our total spend in 2024?");
check("T8 declined", (await card.locator(".badge").innerText()) === "Cannot answer");

// 6. Clarification without options -> edit the question
card = await askAndWait("Who can approve USD 55,000?");
check("FX tier clarification", (await card.locator(".badge").innerText()) === "Needs clarification");
await card.getByRole("button", { name: "Edit my question" }).click();
check("Edit puts question back in the input", (await page.getByLabel("Question").inputValue()) === "Who can approve USD 55,000?");

// 6b. Follow-up: the last answered question is sent as context
card = await askAndWait("Which invoices of EUR 1,000 or more have no PO?");
check("missing-PO question answered", (await card.locator(".answer").innerText()).startsWith("21 invoices"));
card = await askAndWait("How about just the invoices from CC-FIN-100?");
const fuText = await card.innerText();
check("follow-up answered", (await card.locator(".badge").innerText()) === "Answered");
check("follow-up label shows previous question", fuText.includes("Follow-up to: “Which invoices of EUR 1,000 or more have no PO?”"));
check("follow-up = 10 missing-PO invoices, EUR 383,083.04", fuText.includes("10 invoices at or above the PO threshold") && fuText.includes("EUR 383,083.04"));
await card.screenshot({ path: `${SHOTS}07-follow-up.png` });

// 7. Network failure (simulated by aborting the request in the browser)
await page.route("**/api/ask", (route) => route.abort());
await page.getByLabel("Question").fill("What is our total spend with vendors based in Spain?");
await page.getByLabel("Question").press("Enter");
await lastCard().locator(".badge").waitFor();
card = lastCard();
check("network failure -> error card", (await card.innerText()).includes("Could not reach the API"));
check("network failure -> Retry", await card.getByRole("button", { name: "Retry" }).isVisible());
await card.screenshot({ path: `${SHOTS}06-network-error.png` });
await page.unroute("**/api/ask");

// 8. Real LLM failure (backend with unreachable LLM endpoint): summary still loads, /ask fails closed
if (BROKEN) {
  const p2 = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2 });
  await p2.goto(BROKEN);
  await p2.addStyleTag({ content: ".composer { position: static; }" });
  await p2.getByText("net spend ·").waitFor({ timeout: 15_000 });
  check("LLM down: summary still loads", true);
  await p2.getByLabel("Question").fill("What is our total spend with vendors based in Spain?");
  await p2.getByLabel("Question").press("Enter");
  const c2 = p2.locator("article.turn").last();
  await c2.locator(".badge").waitFor({ timeout: 60_000 });
  const t = await c2.innerText();
  check("LLM down: error badge", (await c2.locator(".badge").innerText()).startsWith("Error"));
  check("LLM down: error code shown, no partial result", t.includes("llm_unreachable") && t.includes("Nothing above is a partial result"));
  check("LLM down: no evidence shown", (await c2.locator("details").filter({ hasText: "Invoice rows used" }).count()) === 0);
  await c2.screenshot({ path: `${SHOTS}05-llm-error.png` });
}

await browser.close();
console.log(failures ? `\n${failures} check(s) FAILED` : "\nAll UI checks passed");
process.exit(failures ? 1 : 0);
