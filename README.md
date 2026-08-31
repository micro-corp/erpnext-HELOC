# HELOC Tracker

A small custom Frappe app for tracking a borrower-side hybrid HELOC (Revolving + Prêt Lié tranches)
end-to-end — built as the mirror image of ERPNext's Loan Management module, which is lender-oriented
and doesn't fit tracking your own liability.

## What it does

**HELOC Facility** — one record per overall HELOC (e.g. "Marge Atout - Desjardins"). This is the
global view: Credit Limit, Total Balance (live rollup across every linked tranche), and Available
Credit, plus a "Tranches" connections tab listing every linked HELOC Tranche.

- **Refresh Balance** button — recomputes Total Balance / Available Credit from all linked tranches
  on demand (also happens automatically after every tranche payment or carve-out).
- **Carve Out New Tranche** button — the guided workflow for splitting part of the Revolving Portion
  into a new Fixed tranche in one action: creates the HELOC Tranche record, generates its amortization
  schedule, and posts a single Journal Entry (debit Revolving liability, credit new tranche liability)
  — matching the carve-out convention already on your Chart of Accounts. Reduces the Revolving
  tranche's balance and refreshes the facility rollup automatically.

**HELOC Tranche** — one record per tranche (each Prêt Lié tranche, or the Revolving Portion), always
linked to a parent HELOC Facility, with its own liability/interest GL accounts and bank account. Its
payments live as their own **HELOC Amortization Entry** documents (see below), linked back via a
Tranche field and shown on a "Payments" connections tab.

- **Generate Schedule** button — for Fixed tranches, runs standard level-payment amortization math
  from Original Principal / Rate / Term / Start Date and creates one **Draft** HELOC Amortization
  Entry per payment. Nothing posts to the GL yet — see below.
- **Add Manual Payment** button — opens a new HELOC Amortization Entry (Entry Type: Manual) with
  Tranche and Opening Balance pre-filled. The only way to add a payment on a Revolving tranche; also
  useful for corrections, back-entries, or off-schedule payments on a Fixed tranche.
- **Post Next Payment** button — a convenience shortcut that Submits the earliest Draft entry for that
  tranche (see below for what Submit does).
- Only one Revolving tranche is allowed per Facility (validated on save).
- Revolving tranches are excluded from schedule generation (rate and balance both float, so no fixed
  formula fits) — use Add Manual Payment each statement instead.

**HELOC Amortization Entry** — a standalone, **submittable** document: one per payment, linked to a
Tranche. This is where posting actually happens:

- **Draft** — not posted. Fully editable/deletable, and this is what makes Generate Schedule double as
  a simulation: the whole projected schedule shows up in the Tranche's charts and totals immediately,
  with zero GL impact, so you can review the full payoff curve before committing to any of it.
- **Submit** — the only thing that posts. Creates and submits the Journal Entry (debit interest +
  principal, credit bank), links it back onto the entry, and updates the Tranche's Current Balance
  (which cascades up to the Facility's rollup). After Submit, the entry's figures are locked — correct
  it via Cancel + Amend, standard Frappe submittable behavior.
- **Cancel** — reverses a Submitted entry, but only if it's the most recently posted one on that
  tranche (balances have to unwind in the same order they were posted in).

## End-to-end flow

1. Create a **HELOC Facility** record (Credit Limit, Company, Lender).
2. Create the **Revolving** HELOC Tranche linked to it (accounts + starting balance).
3. Create each **Fixed (Prêt Lié)** tranche linked to it, or use **Carve Out New Tranche** from the
   Facility to split funds out of the Revolving Portion into a new one on the fly.
4. Each month: open each tranche, Submit the next Draft entry (directly, or via **Post Next
   Payment**), review the Journal Entry.
5. Check the Facility record any time for Total Balance / Available Credit — it's always current.

## Install

From your bench directory:

```bash
bench get-app heloc_tracker /path/to/unzipped/heloc_tracker
bench --site your-site-name install-app heloc_tracker
bench --site your-site-name migrate
bench build --app heloc_tracker
bench restart
```

(`get-app` here takes a local path since this isn't published to a git remote — adjust if you push it
to your own git repo instead, in which case pass the repo URL. Compatible with Frappe v16, declared
in `pyproject.toml` under `[tool.bench.frappe-dependencies]`. The `bench build` step is required for
the app's icon on the Desktop apps list to show up — static assets under `public/` aren't served until
built, so skipping this step is why the tile shows blank.)

## Usage

1. Create a **HELOC Facility** — Credit Limit, Company, Lender.
2. Create a **HELOC Tranche** per tranche, linked to that Facility:
   - Revolving: fill the accounts, no schedule to generate — use **Add Manual Payment** as statements
     come in.
   - Fixed tranches: fill Original Principal, Interest Rate, Term (Months), Start Date, and the three
     accounts (Liability, Interest Expense, Bank). Click **Generate Schedule** — this creates Draft
     entries you can review before posting anything.
   - Alternatively, use **Carve Out New Tranche** on the Facility to create a Fixed tranche directly
     out of the Revolving balance, with the moving-balance Journal Entry posted automatically.
3. Each month, Submit the next Draft HELOC Amortization Entry (open it directly, or use **Post Next
   Payment** on the Tranche). Review the Journal Entry it creates.
4. Current Balance (tranche) and Total Balance / Available Credit (facility) stay in sync
   automatically — no manual rollup math needed.

## v16 compatibility

Checked against the official Frappe Framework v16 and ERPNext v16 migration guides
(github.com/frappe/frappe/wiki/Migrating-to-version-16 and
github.com/frappe/erpnext/wiki/Migration-Guide-To-ERPNext-Version-16, both current as of Aug 2026):

- **Doctype default sort**: v16 changed the framework default from `modified` to `creation` for
  list views/`get_all`/`get_list`. All doctype JSONs here already use `"sort_field": "creation"`.
- **Journal Entry fields**: `debit_in_account_currency` / `credit_in_account_currency` on the
  `Journal Entry Account` child table are unchanged in v16 — confirmed against the current
  `develop`/v16 source, so both the posting logic in `HELOCAmortizationEntry.post_to_gl()` and
  `carve_out_tranche()`'s JE construction are fine as written.
- **Removed/changed whitelisted methods in v16** (`make_bank_account`, `make_pricing_rule`,
  Timesheet-billing-via-API) don't overlap with anything this app calls, so no changes needed there.
- **`add_to_apps_screen` hook** (v16 Desktop icon grid): implemented in `hooks.py`, route points at
  the HELOC Facility list (`/app/heloc-facility`) since that's now the top-level entity. I haven't
  been able to confirm that exact route resolves correctly post the v16 `/app` → `/desk` rerouting
  change on a live instance — adjust if the tile lands somewhere unexpected.
- **Requires Python 3.14+ / NodeJS 24+** on the bench itself for v16 — an environment requirement,
  not something this app's code needs to account for.
- Not affected by the v16 permission-hook or query-builder changes since this app defines no
  `has_permission` hooks and does no raw SQL — `frappe.get_all(..., pluck=...)` is the standard
  query-builder-backed API, unaffected by the refactor.

## Notes / things to sanity-check before relying on this

- I still haven't run this against a live v16 instance — the checks above are a documentation-level
  validation, not a functional test. Test on a staging site first, particularly the Journal Entry
  account/debit/credit wiring on both `post_next_payment` and `carve_out_tranche`, before pointing it
  at real data.
- No fixtures or scheduled jobs are installed — every posting action (payment or carve-out) is always
  a manual button click, on purpose.
