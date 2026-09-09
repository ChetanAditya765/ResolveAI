import { expect, test, type Page as BrowserPage } from "@playwright/test";
import type { AgentRun, Page, Ticket, ToolExecution } from "../types/api";

async function identity(page: BrowserPage, name: string) {
  const selector = page.getByLabel("Demo identity", { exact: true });
  await expect(selector).toBeEnabled();
  const option = selector.locator("option").filter({ hasText: name });
  const value = await option.getAttribute("value");
  await selector.selectOption(value!);
  await expect(selector).toBeEnabled();
  await expect(selector).toHaveValue(value!);
}
async function createTicket(
  page: BrowserPage,
  employee: string,
  request: string,
) {
  await page.goto("/tickets/new");
  await page.getByLabel("Employee", { exact: true }).selectOption(employee);
  await page.getByLabel("Access request", { exact: true }).fill(request);
  await page
    .getByRole("button", { name: "Submit request", exact: true })
    .click();
  await expect(page).toHaveURL(/\/tickets\/[a-f0-9-]+$/);
  return page.url().split("/").pop()!;
}
async function persistedRun(page: BrowserPage, id: string): Promise<AgentRun> {
  const response = await page.request.get(`/api/tickets/${id}/runs?limit=1`);
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as Page<AgentRun>).items[0];
}
async function assertOutcome(
  page: BrowserPage,
  id: string,
  status: string,
  forbidden: string[] = [],
) {
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/tickets/${id}`)).json()).status,
    )
    .toBe(status);
  const run = await persistedRun(page, id);
  const tools = (await (
    await page.request.get(`/api/agent-runs/${run.id}/tools?limit=100`)
  ).json()) as Page<ToolExecution>;
  for (const name of forbidden)
    expect(tools.items.map((tool) => tool.tool_name)).not.toContain(name);
  return { run, tools };
}

test("payments write access pauses, manager approves, verification closes the ticket", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const id = await createTicket(
    page,
    "EMP001",
    "I need write access to the payments repository.",
  );
  await expect(
    page.getByRole("heading", { name: "Waiting for approval", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve access", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Repository Access Policy", { exact: true }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/screenshots/ticket-waiting.png",
    fullPage: true,
  });
  await identity(page, "Maya Rao");
  await page.getByRole("link", { name: "Approvals", exact: true }).click();
  const card = page.getByTestId(`approval-${id}`);
  await expect(card.getByText("Chetan Aditya", { exact: true })).toBeVisible();
  await expect(
    card.getByText("Grant write access to payments", { exact: true }),
  ).toBeVisible();
  await card
    .getByLabel("Decision comment (optional)")
    .fill("Business need confirmed for payments development.");
  await page
    .getByRole("heading", { name: "Approval inbox", exact: true })
    .click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "test-results/screenshots/approval.png",
    fullPage: true,
  });
  const decision = page.waitForResponse(
    (response) =>
      response.url().endsWith("/approve") &&
      response.request().method() === "POST",
  );
  await card
    .getByRole("button", { name: "Approve access", exact: true })
    .click();
  expect((await decision).status()).toBe(200);
  await page.goto(`/tickets/${id}`);
  await expect(page.getByTestId("final-response")).toContainText("write");
  await expect(
    page.getByRole("heading", { name: "Ticket resolved", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("verified-permission")).toHaveText("write");
  const { run, tools } = await assertOutcome(page, id, "RESOLVED");
  expect(run.state.outcome).toBe("granted");
  expect(run.state.verification_result?.sufficient).toBe(true);
  expect(run.state.approval_status).toBe("APPROVED");
  expect(tools.total).toBe(10);
  await expect(page.getByTestId("run-task-success")).toHaveText("100.0%");
  for (const check of [
    "policy_evidence",
    "approval_before_grant",
    "tool_scope",
    "verification_before_close",
  ]) {
    await expect(page.getByTestId(`run-check-${check}`)).toContainText(
      "Passed",
    );
  }
  expect(
    tools.items.filter(
      (tool) => tool.tool_name === "grant_repository_permission",
    ),
  ).toHaveLength(1);
  expect(
    tools.items.filter(
      (tool) => tool.tool_name === "get_repository_permission",
    ),
  ).toHaveLength(2);
  await page.screenshot({
    path: "test-results/screenshots/ticket-resolved.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /Tool executions/ }).click();
  await page
    .locator("summary")
    .filter({ hasText: "grant_repository_permission" })
    .click();
  await expect(
    page.locator(".tool-body").filter({ hasText: '"changed": true' }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("verified-permission")).toHaveText("write");
  await expect(page.getByTestId("run-task-success")).toHaveText("100.0%");
  await page.goto("/");
  await expect(
    page.getByRole("link", {
      name: /I need write access to the payments repository/,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/screenshots/dashboard.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("rejection escalates without changing permission", async ({ page }) => {
  const id = await createTicket(
    page,
    "EMP007",
    "I need write access to the payments repository.",
  );
  await expect(
    page.getByRole("heading", { name: "Waiting for approval", exact: true }),
  ).toBeVisible();
  await identity(page, "Maya Rao");
  const card = page.getByTestId(`approval-${id}`);
  await card
    .getByLabel("Decision comment (optional)")
    .fill("No current business need.");
  await card
    .getByRole("button", { name: "Reject request", exact: true })
    .click();
  await expect(page.getByTestId("final-response")).toBeVisible();
  const { run } = await assertOutcome(page, id, "ESCALATED", [
    "grant_repository_permission",
    "close_ticket",
  ]);
  expect(run.state.outcome).toBe("rejected");
  expect(run.state.approval_status).toBe("REJECTED");
});

test("same-department read is granted automatically", async ({ page }) => {
  const id = await createTicket(
    page,
    "EMP007",
    "I need read access to the payments repository.",
  );
  await expect(page.getByTestId("verified-permission")).toHaveText("read");
  const { run } = await assertOutcome(page, id, "RESOLVED", [
    "create_approval_request",
  ]);
  expect(run.state.outcome).toBe("granted");
});

test("existing sufficient permission resolves without a grant", async ({
  page,
}) => {
  const id = await createTicket(
    page,
    "EMP002",
    "I need read access to the payments repository.",
  );
  await expect(page.getByTestId("verified-permission")).toHaveText("write");
  const { run } = await assertOutcome(page, id, "RESOLVED", [
    "grant_repository_permission",
    "create_approval_request",
  ]);
  expect(run.state.outcome).toBe("already_sufficient");
});

test("admin access escalates without approval or grant", async ({ page }) => {
  const id = await createTicket(
    page,
    "EMP001",
    "I need admin access to the platform-api repository.",
  );
  await expect(page.getByTestId("final-response")).toBeVisible();
  await assertOutcome(page, id, "ESCALATED", [
    "grant_repository_permission",
    "create_approval_request",
    "close_ticket",
  ]);
});

test("unknown repository is not invented", async ({ page }) => {
  const id = await createTicket(
    page,
    "EMP001",
    "I need write access to the moon-base repository.",
  );
  await expect(page.getByTestId("final-response")).toBeVisible();
  await assertOutcome(page, id, "ESCALATED", [
    "grant_repository_permission",
    "create_approval_request",
  ]);
});

test("failed run submission retries the saved ticket without duplication", async ({
  page,
}) => {
  const count = (
    (await (
      await page.request.get("/api/tickets?limit=1")
    ).json()) as Page<Ticket>
  ).total;
  await page.route(
    "**/api/tickets/*/run",
    async (route) => {
      await route.fulfill({
        status: 503,
        json: {
          error: {
            code: "temporary_failure",
            message: "Agent service temporarily unavailable.",
          },
        },
      });
    },
    { times: 1 },
  );
  await page.goto("/tickets/new");
  await page.getByLabel("Employee", { exact: true }).selectOption("EMP002");
  await page
    .getByLabel("Access request", { exact: true })
    .fill("I need read access to the payments repository.");
  await page
    .getByRole("button", { name: "Submit request", exact: true })
    .click();
  await expect(
    page.getByText("Your ticket was saved.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByLabel("Employee", { exact: true })).toBeDisabled();
  await page
    .getByRole("button", { name: "Retry agent run", exact: true })
    .click();
  await expect(page).toHaveURL(/\/tickets\/[a-f0-9-]+$/);
  await expect(page.getByTestId("final-response")).toBeVisible();
  expect(
    (
      (await (
        await page.request.get("/api/tickets?limit=1")
      ).json()) as Page<Ticket>
    ).total,
  ).toBe(count + 1);
});

test("switching identities clears another reviewer's approval view", async ({
  page,
}) => {
  const id = await createTicket(
    page,
    "EMP004",
    "I need write access to the finance-reporting repository.",
  );
  await expect(
    page.getByRole("heading", { name: "Waiting for approval", exact: true }),
  ).toBeVisible();
  await identity(page, "Kabir Shah");
  await page.goto("/approvals");
  await expect(page.getByTestId(`approval-${id}`)).toBeVisible();
  await identity(page, "Chetan Aditya");
  await expect(page.getByTestId(`approval-${id}`)).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "No pending approvals" }),
  ).toBeVisible();
});

test("backend failure is visible and retry recovers the queue", async ({
  page,
}) => {
  await page.route("**/api/tickets?**", (route) =>
    route.fulfill({
      status: 502,
      json: { error: { message: "Backend unavailable for test." } },
    }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert").first()).toContainText(
    "Backend unavailable for test.",
  );
  await page.unroute("**/api/tickets?**");
  await page
    .getByRole("button", { name: "Retry", exact: true })
    .first()
    .click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("table")).toBeVisible();
});

test("mobile layout and policy inspection work", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Access, with accountability." }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "test-results/screenshots/mobile.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Policy library", exact: true }).click();
  await page
    .locator("summary")
    .filter({ hasText: "Repository Access Policy" })
    .click();
  await expect(
    page
      .locator(".policy-document[open] .policy-document-body")
      .filter({ hasText: "manager approval" })
      .first(),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
});

test("evaluation batches persist real scores and isolate scenario tickets", async ({
  page,
}) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const before = await (
    await page.request.get("/api/tickets?limit=100")
  ).json();
  const catalog = await (
    await page.request.get("/api/evaluations/scenarios")
  ).json();
  expect(catalog.length).toBeGreaterThanOrEqual(25);
  await page.goto("/evaluations?source=scenario");
  await expect(
    page.getByRole("heading", { name: "Evaluation workspace" }),
  ).toBeVisible();
  await expect(page.getByTestId("metric-task_success")).toContainText("N/A");
  await expect(page.getByRole("button", { name: /Run all/ })).toHaveCount(0);
  await identity(page, "Maya Rao");
  const submission = page.waitForResponse(
    (response) =>
      response.url().endsWith("/evaluations/run") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: `Run all ${catalog.length} scenarios` })
    .click();
  const response = await submission;
  expect(response.status()).toBe(202);
  const batch = await response.json();
  await expect(page).toHaveURL(new RegExp(`batch=${batch.id}`));
  await expect(page.getByTestId("evaluation-batch-progress")).toContainText(
    "Completed",
    { timeout: 120000 },
  );
  await expect(page.getByTestId("evaluation-summary-counts")).toContainText(
    `${catalog.length} evaluated scenarios`,
  );
  for (const key of [
    "task_success",
    "policy_compliance",
    "approval_compliance",
    "tool_selection_accuracy",
    "escalation_correctness",
  ]) {
    await expect(page.getByTestId(`metric-${key}`)).toContainText("100.0%");
    await expect(page.getByTestId(`metric-${key}`)).toContainText(
      `${catalog.length} scored runs`,
    );
  }
  await expect(
    page.getByTestId("metric-hallucination_or_invalid_resource_rate"),
  ).toContainText("0.0%");
  await page.screenshot({
    path: "test-results/screenshots/evaluations.png",
    fullPage: true,
  });
  const firstResult = page.locator(".evaluation-result").first();
  await firstResult.locator(":scope > summary").click();
  await firstResult
    .getByRole("button", { name: "Inspect stored trace" })
    .click();
  await expect(
    firstResult.getByTestId("stored-evaluation-trace"),
  ).toContainText('"tool_name"');
  await page.reload();
  await expect(page.getByTestId("evaluation-batch-progress")).toContainText(
    `${catalog.length} / ${catalog.length}`,
  );
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.locator(".pagination")).toContainText(
    `11–20 of ${catalog.length}`,
  );
  const after = await (await page.request.get("/api/tickets?limit=100")).json();
  expect(after.total).toBe(before.total);
  expect(after.items.map((item: Ticket) => item.id).sort()).toEqual(
    before.items.map((item: Ticket) => item.id).sort(),
  );
  await page
    .getByRole("link", { name: "Live agent runs", exact: true })
    .click();
  await expect(page.getByTestId("metric-escalation_correctness")).toContainText(
    "N/A",
  );
  await expect(page.getByTestId("metric-escalation_correctness")).toContainText(
    "0 scored runs",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  expect(errors).toEqual([]);
});

test("identity connection can recover after a startup outage", async ({
  page,
}) => {
  await page.route(
    "**/api/demo/users",
    (route) =>
      route.fulfill({
        status: 502,
        json: {
          error: { message: "Identity service temporarily unavailable." },
        },
      }),
    { times: 1 },
  );
  await page.goto("/approvals");
  await expect(
    page.getByRole("button", { name: "Retry identity connection" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry identity connection" }).click();
  await expect(page.getByLabel("Demo identity", { exact: true })).toBeEnabled();
  await expect(
    page.getByRole("heading", { name: "No pending approvals" }),
  ).toBeVisible();
});
