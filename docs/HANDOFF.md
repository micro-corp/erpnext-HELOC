# HELOC Tracker — Handoff

**Repo:** `https://github.com/micro-corp/erpnext-HELOC` (branch `main`)
**Local state as of this handoff:** commit `a2dc0cd` was pushed (or pending push — confirm), **plus
an uncommitted/unpushed submit-lifecycle refactor on top of it** — see §1a and §6 below. Needs a
fresh GitHub token to push.
**Target environment:** ERPNext v16 (confirmed live versions: frappe 16.31.0, erpnext 16.32.3), self-hosted.

---

## 1. What this app does

Borrower-side liability tracking for a Desjardins Marge Atout (hybrid HELOC: variable Revolving
Portion + fixed-rate Prêt Lié tranches), built because ERPNext's native Loan Management module is
lender-oriented and doesn't fit tracking your own debt.

Three doctypes:
- **HELOC Facility** — one per HELOC. Credit limit, live Total Balance/Available Credit rollup,
  Cost Center, Credit Limit memo posting, aggregate rollup stats, aggregate burndown chart.
- **HELOC Tranche** — one per Revolving Portion or Fixed tranche. Own GL accounts, a "Payments"
  connection listing its HELOC Amortization Entry docs, per-tranche rollup, per-tranche charts.
- **HELOC Amortization Entry** — **standalone submittable doctype** (not a child table — see §1a),
  one per payment. Links back to its Tranche via a `tranche` field.

Full field-by-field GL account mapping and setup order: **`docs/CONFIGURATION.md`** in the repo —
that document is kept current and is the primary reference, not this handoff.

## 1a. This session's change: HELOC Amortization Entry is now submittable

Previously, HELOC Amortization Entry was a child table row on the Tranche with a `posted` checkbox
standing in for "has this been posted to the GL." This session converted it into a standalone
`is_submittable: 1` doctype using real Draft (0) / Submitted (1) / Cancelled (2) `docstatus`, at
Jonathan's request: **Submit should be what triggers posting, manual payment entry should be
supported, and Draft status should double as a simulation mode.**

What changed, concretely:

- **`before_submit()`** on the entry now does what `Tranche.post_next_payment()` used to do inline:
  row-locks the tranche, validates accounts + opening balance, builds and submits the Journal Entry,
  updates the tranche's Current Balance. This is the *only* code path that posts anything — there's
  no other trigger.
- **`on_cancel()`** reverses it — cancels the linked JE and rolls the tranche's Current Balance back
  — but only when the entry being cancelled is the *most recently posted* one on that tranche (i.e.
  its Closing Balance still equals the tranche's Current Balance). This is stricter than the old
  child-row code, which would happily revert the balance to whatever row you cancelled regardless of
  posting order — a latent correctness bug in the original design that this refactor fixes as a side
  effect, not just preserves.
- **`Tranche.generate_schedule()`** now creates a batch of Draft HELOC Amortization Entry documents
  instead of appending child rows. Since Draft entries have zero GL impact but still feed the
  Tranche/Facility charts and rollup stats, this is what gives you "simulation": generate a schedule,
  look at the full projected payoff curve and total interest cost, and only submit entries as real
  statements actually arrive. Delete and regenerate freely while everything's still Draft.
- **`Tranche.post_next_payment()`** is now a thin convenience wrapper — finds the earliest Draft entry
  for the tranche and calls `.submit()` on it. Kept for one-click UX continuity; the actual posting
  logic lives entirely on the entry doctype now, not here.
- **New "Add Manual Payment" button** on the Tranche form opens a new entry pre-filled with Tranche
  and current Opening Balance, `entry_type` defaulted to "Manual." Manual entries post through the
  exact same Submit path as Scheduled ones — the field is just a label, with one functional side
  effect: it's the only entry_type that exposes **Skip Opening Balance Check**, for intentional
  corrections/back-entries.
- **`journal_entry_hooks.on_cancel`** now cancels the linked HELOC Amortization Entry (letting its own
  `on_cancel` do the balance reversal) instead of directly manipulating a child row. No recursion risk:
  by the time this hook runs, the JE is already at docstatus 2, so the entry's own attempt to re-cancel
  the JE inside its `reverse_posting()` is a no-op.
- **`HELOCFacility.get_rollup_stats()` / `get_burndown_data()`** updated from `parent`/`posted`
  filters to `tranche`/`docstatus` filters against the now-standalone doctype. Burndown semantics are
  otherwise unchanged (Draft entries were already included in the projection before this refactor,
  since the old code never filtered on `posted` there either — so the "simulation shows up on the
  chart" behavior isn't new, it's just now paired with an actual Draft/Submit lifecycle instead of an
  ungated checkbox).

**Not done this session:** this was built and syntax-checked (Python AST, JSON parse, Node
`--check` on the JS) but **never run against a live bench.** Same bar as the rest of this app's
history — see §4.

---

## 2. Current state — what's solid

- Core amortization math (level-payment, Tranche Term vs Total Amortization split for balloon/renewal
  scenarios) — verified with standalone Python tests, including the 0%-rate edge case and the
  balloon-vs-fully-amortizing cases. Unaffected by this session's submit-lifecycle refactor.
- Journal Entry posting (`before_submit()` on HELOC Amortization Entry, `Carve Out New Tranche`,
  `Post Credit Limit`) — correct debit/credit wiring, documented in `docs/CONFIGURATION.md` §6.
- Data integrity: submitted-entry immutability (via Frappe's own submittable-doctype behavior, not
  custom code), last-in-first-out cancellation ordering, account/company/root-type validation on
  every Account link (client + server side), row-locking (`SELECT ... FOR UPDATE`) against
  double-posting races, deletion guards, Closed-status validation, JE-cancellation reconciliation
  (`journal_entry_hooks.py`).
- Charts: per-Tranche Burndown + Principal-vs-Interest, per-Facility aggregate Burndown (properly
  merges tranches with different payment dates onto one timeline, not a naive per-date sum — verified
  with a staggered-dates test). Now sourced via `Tranche.get_schedule_rows()` / Facility's existing
  whitelisted methods against the standalone doctype, rather than reading an embedded child table.
- Rollup stats: per-Tranche and per-Facility (Beginning/Ending Balance, Total Principal/Interest,
  Submitted-to-date figures).
- App tile icon: PNG (not SVG — SVG was rendering as a broken-image glyph on the live instance for
  reasons never fully diagnosed; PNG generated via Pillow, confirmed to render correctly).
- v16 compatibility: `sort_field: creation`, `pyproject.toml` (not `setup.py`/`requirements.txt`,
  `frappe` never listed as a pip dependency — that caused a real Docker build failure earlier in this
  project, root-caused and fixed).

## 3. Known-broken / intentionally disabled

- **Budget integration is OFF.** `sync_budget()` is a stub that throws an explanatory error. The
  original design assumed a Budget schema (one doc per Cost Center with a multi-account child table,
  single `fiscal_year`) that does **not** match this instance's actual v16 schema (one Budget doc per
  single account, `from_fiscal_year`/`to_fiscal_year` range). Confirmed by fetching the real
  `budget.py` from `frappe/erpnext` `develop` branch, not inferred from docs or snippets.
  **Hard blocker found in that source:** `Budget.validate_account()` rejects any account whose
  `report_type` isn't "Profit and Loss" — **a Tranche's Liability Account (principal) can never be
  budgeted via ERPNext's Budget doctype, no override exists.** Only interest ever could be. Full
  writeup: `docs/CONFIGURATION.md` §10. Do not re-enable this without redesigning around the real
  per-account schema and deciding how (or whether) to represent principal tracking separately.
- **Cost Center field is still functional** independent of the above — every real payment JE tags it,
  usable for filtering in ERPNext's own reports even without a formal Budget document.
- **No data migration path for existing amortization data.** If any HELOC Amortization Entry rows
  already exist live under the old child-table schema, `bench migrate` on the new schema (istable 0,
  is_submittable 1, new `tranche` field replacing `parent`) will need a manual migration step - this
  wasn't built, since it's unknown whether there's live data to migrate. Check before running
  `bench migrate` on an instance with existing HELOC data; if there is any, back up the database first
  and plan the migration (likely a one-off patch script) before proceeding.

## 4. Uncertain / needs live verification

Nothing in this app has been run against a live bench by me at any point — everything is validated by
syntax-checking (Python AST, JSON parse, JS `Function()` construction), standalone logic tests
(amortization math, burndown merge logic), and cross-checking field names against real Frappe/ERPNext
source fetched directly from GitHub. That's a meaningfully different bar than "tested live," and
several rounds of real bugs (v16 `pyproject.toml`/build failure, the Budget schema mismatch, the
collapsible-section chart bug, the broken SVG icon) were only caught because Jonathan reported actual
runtime behavior back. Assume more of these exist until proven otherwise on staging.

Specific open questions:
- Icon: PNG fix is unverified live — last report was the SVG rendering as broken-image. If PNG is
  *still* wrong, the likely next suspect is `bench build --app heloc_tracker` not having been run
  (static assets aren't served until built), not the asset itself.
- Chart "still showing with no data" — the guard code already existed before the report; most likely
  explanation is stale JS bundle (same `bench build` class of issue), not a logic gap. Worth
  confirming on a fresh build before looking for a code bug that may not exist.
- Row-locking (`for_update=True`) and the JE cancel/reconcile hooks are logically reviewed but never
  exercised against concurrent real requests.
- **New this session, all unverified live:**
  - The `autoname: "HAE-.#####"` shorthand on a submittable doctype — should work per Frappe's
    autoname conventions, but hasn't been confirmed against a real bench.
  - Whether `bench migrate` cleanly converts the doctype in place (istable 1→0, is_submittable 0→1,
    field additions/removals) if any live data already exists under the old schema — see §3.
  - The connections "Payments" tab on the Tranche form (via the `links` array pointing at
    `HELOC Amortization Entry.tranche`) — this is the standard Frappe pattern (same as Facility's
    existing "Tranches" tab, which is presumably already confirmed working), but not independently
    re-verified for this doctype.
  - `frappe.db.count()` client-side (used in the new `heloc_tranche.js` to decide which buttons to
    show) — should be a standard client API, not confirmed against this bench's frappe version.
  - The last-in-first-out cancellation guard in `HELOCAmortizationEntry.reverse_posting()` — logically
    reviewed, matches the intended invariant, but never exercised against a real cancel from either
    the entry side or the Journal Entry side.

## 5. Design decisions worth knowing before changing anything

- **Carve-out JEs are never tagged with a Cost Center** and **never counted in rollup "posted"
  figures the same way payment JEs are** — a carve-out is a balance reclassification between two
  liability accounts, not an actual principal payment. Don't fold it into "actual spend" logic.
- **Revolving tranches don't get amortization schedules generated** — rate and balance both float, no
  fixed formula fits. Entries are added by hand via **Add Manual Payment** each statement.
  `generate_schedule()` refuses to run on a Revolving tranche.
- **Only one Revolving tranche allowed per Facility** (validated). Fixed tranches normally originate
  from a Revolving carve-out; creating one manually without a Revolving tranche present triggers a
  non-blocking warning, not a hard stop (historical-data-import is a legitimate exception).
- **Tranche Term vs Total Amortization** — Term is how many entries get generated (rate lock
  length); Total Amortization is the payoff horizon used to *calculate* the payment. If Term <
  Amortization, the schedule intentionally leaves a real balloon balance at the last entry rather than
  forcing it to zero.
- **Draft = simulation, by design, not by accident.** `generate_schedule()` deliberately stops at
  creating Draft entries rather than posting anything — this is what makes it double as a projection
  tool. Don't "helpfully" auto-submit the first entry or otherwise short-circuit this; the whole point
  is that a generated schedule is inert until you choose to post pieces of it.
- **Cancellation is last-in-first-out, not row-by-row.** Because Current Balance is a running total
  built by walking forward one submitted entry at a time (not stored independently per entry), only
  the most-recently-posted entry on a tranche can be cancelled at any given moment. This is enforced
  in `reverse_posting()` regardless of which side triggers the cancel (the entry itself, or the linked
  Journal Entry directly). Don't relax this without also changing how Current Balance is derived.
- GitHub push workflow: no persistent credentials are stored anywhere in this environment by design —
  every push in this project has required a fresh token pasted in chat, used once, and immediately
  stripped from the local git remote config afterward. That's intentional, not an oversight to fix.

## 6. Immediate next steps

1. Push the local commit(s), including this session's submit-lifecycle refactor (needs a fresh token).
2. **Before running `bench migrate` on any instance with existing HELOC data:** check whether any
   HELOC Amortization Entry rows already exist under the old child-table schema. If so, back up the
   database and plan a migration step first — see §3. If this is a clean instance with no prior
   amortization data, `bench migrate` should be safe to run directly.
3. On the live instance: `bench build --app heloc_tracker`, then `bench migrate`, then hard-verify:
   icon renders, charts render/hide correctly with Draft-only, mixed, and no data, rollups populate,
   the "Payments" connection tab shows up on Tranche, "Create Revolving Tranche" still appears only
   when appropriate.
4. **New verification specific to this session's change** — walk through the full lifecycle on
   staging before trusting it live:
   - Generate Schedule on a test Fixed tranche → confirm Draft entries appear, charts/rollups reflect
     them, nothing hits the GL.
   - Submit one entry (both via the entry's own Submit button and via Post Next Payment) → confirm
     the JE posts correctly and Current Balance updates.
   - Add Manual Payment on the Revolving tranche → confirm it behaves identically once submitted.
   - Cancel the most recently submitted entry → confirm the JE cancels and balance reverts.
   - Attempt to cancel a non-last entry → confirm it's blocked with the expected error.
   - Cancel a JE directly from the Journal Entry list (not via the entry's Cancel button) → confirm
     the linked HELOC Amortization Entry auto-cancels and balance reconciles, per §9.3/§9.4 in
     `docs/CONFIGURATION.md`.
5. Decide the Budget-integration path (see §3) before any further work there — it's a real open
   design question, not a bug to patch.
