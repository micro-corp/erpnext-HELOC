# HELOC Tracker — Configuration Guide

This guide covers how to set up the HELOC Tracker app in ERPNext: what each field means, which GL
account goes where, and the order to create records in. It assumes the app is already installed
(see the README that shipped with the app zip for install steps).

---

## 1. How the three doctypes fit together

```
HELOC Facility  (one per HELOC, e.g. "Marge Atout - Desjardins")
   │
   ├── HELOC Tranche  (Revolving Portion)
   │       └── HELOC Amortization Entry docs, linked via their Tranche field
   │           (added manually — Revolving has no fixed schedule)
   │
   ├── HELOC Tranche  (Fixed — Prêt Lié Tranche 1)
   │       └── HELOC Amortization Entry docs, linked via their Tranche field
   │           (Scheduled, from Generate Schedule — or Manual, added by hand)
   │
   └── HELOC Tranche  (Fixed — Prêt Lié Tranche 2, 3, ...)
           └── HELOC Amortization Entry docs, same as above
```

- **HELOC Facility** is the global record — credit limit, total balance across everything, available
  credit. You'll only have one of these per actual HELOC.
- **HELOC Tranche** is one record per Revolving Portion or per fixed-rate Prêt Lié tranche. Each one
  points at its own GL accounts.
- **HELOC Amortization Entry** is its own standalone, **submittable** document — not a row inside the
  Tranche anymore. Each one links back to a Tranche via its **Tranche** field. Submit is what actually
  posts a Journal Entry (see §6.1 and §9 below); a Draft entry hasn't touched the GL. Find a Tranche's
  entries via the **Payments** connection on the Tranche form, or by filtering the HELOC Amortization
  Entry list on Tranche.

---

## 2. Chart of Accounts — what you need before you start

Set these up on your COA first. Using the numbering already in place as the example:

| Account | Number | Type | Used by |
|---|---|---|---|
| Marge Atout - Desjardins (group) | 21000 | Group | Reference only — see §4 |
| Revolving Portion (child) | 21010 | Liability | Revolving Tranche → **Liability Account** |
| Prêt Lié (group) | 21020 | Group | Not referenced directly by the app |
| Tranche N (child, one per fixed tranche) | 2102N | Liability | Each Fixed Tranche → **Liability Account** |
| HELOC Interest - Revolving | 55xxx | Expense | Revolving Tranche → **Interest Expense Account** |
| HELOC Interest - Prêt Lié | 55xxx | Expense | Every Fixed Tranche → **Interest Expense Account** (shared, pooled — not per-tranche) |
| Your operating bank account | — | Asset | Every Tranche → **Bank Account** |

Two things worth flagging:

- **Interest stays pooled, principal stays split.** All Fixed tranches point their Interest Expense
  Account at the *same* `HELOC Interest - Prêt Lié` account (matching how Desjardins reports it on
  the statement), but each tranche has its *own* Liability Account, since that's what determines
  individual tranche payoff.
- **The 21000 and 21020 group accounts are not posted to by the app.** The app only ever debits/credits
  the *child* accounts (21010, 2102N). The group accounts exist purely for COA structure and appear
  in reports as parents — you don't link them to any Tranche record.

---

## 3. Setting up the HELOC Facility record

Go to **HELOC Facility → New**.

| Field | What to enter |
|---|---|
| Facility Name | e.g. `Marge Atout - Desjardins` |
| Company | Which ERPNext company this HELOC belongs to |
| Lender | `Desjardins` (defaults to this) |
| Credit Limit | The total facility limit |
| Group Liability Account | *(optional)* Point this at 21000 for documentation purposes. Not posted to — see §2. |
| Total Balance | Leave blank — calculated automatically once tranches exist |
| Available Credit | Leave blank — calculated automatically |
| Credit Limit Asset Account | *(optional)* See §3.1 below |
| Credit Limit Offset Account | *(optional)* See §3.1 below |

Save. Total Balance and Available Credit will show 0 until you add tranches.

### 3.1 Credit Limit memo posting (optional)

If you want the facility's full **Credit Limit** to show up on the GL for reporting purposes — e.g.
to see the unutilized portion of the line alongside what's actually drawn — you can post it as a
balanced memo entry. This is informational only: it never touches the real liability accounts on
your Tranches, and Total Balance / Available Credit are computed independently of it.

You need two accounts on your COA before using this:

| Account | Suggested type | Purpose |
|---|---|---|
| Credit Limit Asset Account | Asset | Debited with the full Credit Limit. Something like "Available Credit - HELOC" |
| Credit Limit Offset Account | Any (commonly a contra under the same liability group) | Credited with the same amount so the entry balances |

Set both fields on the Facility, then click **Post Credit Limit**. This posts one submitted Journal
Entry:

| Line | Account | Debit | Credit |
|---|---|---|---|
| 1 | Credit Limit Asset Account | Credit Limit | — |
| 2 | Credit Limit Offset Account | — | Credit Limit |

The Facility record stores the resulting Journal Entry in **Credit Limit Memo Journal Entry** (read
only). If you change the Credit Limit later, the app will flag on save that the memo entry is now
stale — click **Cancel Credit Limit Posting**, update the limit, then **Post Credit Limit** again.

---

## 4. Setting up the Revolving Portion tranche

Go to **HELOC Tranche → New**.

| Field | What to enter |
|---|---|
| Tranche Name | e.g. `Revolving Portion - Desjardins` |
| HELOC Facility | Link to the Facility from §3 |
| Company / Lender | Auto-filled from the Facility (editable if you need to override) |
| Tranche Type | **Revolving** |
| Liability Account | **21010** |
| Interest Expense Account | **HELOC Interest - Revolving** |
| Bank Account | Your operating bank account |
| Original Principal | Current outstanding balance on the Revolving Portion today |
| Interest Rate | Current variable rate (informational — no schedule is generated from this) |
| Start Date / Term (Months) | Not used for Revolving — leave blank |

Save. Because this is Revolving, **no Generate Schedule button appears** (see §7). Instead, click
**Add Manual Payment** each time you get a statement — this opens a new HELOC Amortization Entry with
Tranche and Opening Balance pre-filled (Entry Type defaults to Manual). Fill in the actual payment
amount, principal, and interest from the statement, then Submit it. See §9 for the full manual-entry
and simulation workflow.

Only **one Revolving tranche is allowed per Facility** — the app blocks saving a second one.

---

## 5. Setting up a Fixed (Prêt Lié) tranche

Go to **HELOC Tranche → New**.

| Field | What to enter |
|---|---|
| Tranche Name | e.g. `Prêt Lié Tranche 1` |
| HELOC Facility | Link to the Facility from §3 |
| Tranche Type | **Fixed (Prêt Lié)** |
| Liability Account | The 2102N child account for this specific tranche |
| Interest Expense Account | **HELOC Interest - Prêt Lié** (same account for every fixed tranche) |
| Bank Account | Your operating bank account |
| Original Principal | The amount carved out into this tranche |
| Interest Rate | The fixed rate locked in for this tranche |
| Start Date | When the tranche was carved out |
| Tranche Term (Months) | How long this rate is locked in for — the number of schedule rows actually generated |
| Total Amortization (Months) | The full payoff horizon used to *calculate* the payment amount, which can be longer than the Tranche Term |

Save, then click **Generate Schedule**. This creates one **Draft** HELOC Amortization Entry per
payment using standard level-payment math (equal payment each month, principal/interest split shifts
over time). Nothing posts to the GL at this point — see §9 for why that's useful (it doubles as a
simulation of the full schedule before you commit to any of it).

### 5.1 Tranche Term vs. Total Amortization

These are two different things, matching standard Canadian mortgage/Prêt Lié convention:

- **Total Amortization** is the full payoff horizon used to *calculate* the level payment amount
  (e.g. 300 months / 25 years).
- **Tranche Term** is how long the current rate is actually locked in for, and is the number of
  schedule rows generated (e.g. 60 months / 5 years).

If Tranche Term equals Total Amortization, the tranche pays itself off to zero exactly like a
standard fully-amortizing loan. If Tranche Term is shorter, the schedule still uses the payment
amount calculated off the full amortization, but only generates rows for the term — leaving a real
remaining balance at the end (visible as the last row's Closing Balance) that gets renewed into a new
rate/tranche, typically via **Carve Out New Tranche** feeding off the Revolving Portion once the
existing tranche's term ends. Tranche Term can never be longer than Total Amortization — the app
blocks that on Generate Schedule.

---

## 6. Where GL postings actually happen

Three things post to the GL. All three create a **submitted Journal Entry** — nothing posts silently
or on a schedule; you always trigger it explicitly (clicking Submit, or a button that submits on your
behalf).

### 6.1 Submitting a HELOC Amortization Entry

**This is the only way a payment posts.** A HELOC Amortization Entry is a Draft until you Submit it —
Submit is what creates and submits the Journal Entry below, links it back onto the entry, and updates
the Tranche's Current Balance. A Draft entry (Scheduled or Manual) has none of this — it's just numbers
sitting on an unposted document. See §9 for the full Draft/Submit/Cancel lifecycle, manual entries, and
using Drafts as a simulation.

| Line | Account | Debit | Credit |
|---|---|---|---|
| 1 | Tranche's Interest Expense Account | Interest portion | — |
| 2 | Tranche's Liability Account | Principal portion | — |
| 3 | Tranche's Bank Account | — | Total payment |

This is the same JE structure for both Revolving (Manual entries) and Fixed (Scheduled or Manual)
tranches. Two ways to trigger it:

- Open the HELOC Amortization Entry directly and click **Submit**.
- From the Tranche, click **Post Next Payment** — a convenience button that finds the earliest Draft
  entry for that tranche and submits it for you. Same underlying action either way.

### 6.2 Carve Out New Tranche (on the Facility)

Moves a chunk of balance from the Revolving Portion into a brand-new Fixed tranche, in one action.

| Line | Account | Debit | Credit |
|---|---|---|---|
| 1 | Revolving tranche's Liability Account (21010) | Carve-out amount | — |
| 2 | New tranche's Liability Account (2102N) | — | Carve-out amount |

No interest or bank movement here — it's purely a balance reclassification between two liability
accounts, matching how a Desjardins Prêt Lié carve-out actually works (no cash changes hands).

After this JE posts, the app automatically:
1. Generates the new tranche's amortization schedule
2. Reduces the Revolving tranche's Current Balance
3. Recalculates the Facility's Total Balance / Available Credit

### 6.3 Post Credit Limit (on the Facility)

Posts the memo entry described in §3.1.

| Line | Account | Debit | Credit |
|---|---|---|---|
| 1 | Credit Limit Asset Account | Credit Limit | — |
| 2 | Credit Limit Offset Account | — | Credit Limit |

One-time per limit amount — re-posting requires cancelling the existing memo entry first
(**Cancel Credit Limit Posting**). This never affects Total Balance or Available Credit; those are
always computed from the Tranches directly, not from this memo entry.

---

## 7. Quick reference — buttons and where they live

| Button | Lives on | What it does |
|---|---|---|
| Generate Schedule | Fixed Tranche only, with no entries yet | Creates the full schedule as Draft HELOC Amortization Entry docs from Principal/Rate/Term/Start Date |
| Add Manual Payment | Any Tranche | Opens a new HELOC Amortization Entry (Entry Type = Manual) with Tranche and Opening Balance pre-filled |
| Post Next Payment | Any Tranche with a Draft entry | Submits the earliest Draft entry — see §6.1 and §9 |
| Submit (on the entry itself) | HELOC Amortization Entry | Posts + submits the JE in §6.1, updates the Tranche's Current Balance |
| Cancel (on the entry itself) | Submitted HELOC Amortization Entry | Reverses the posting — see §9.3 |
| Refresh Balance | Facility | Manually recomputes Total Balance / Available Credit |
| Carve Out New Tranche | Facility | Runs the full workflow in §6.2 |
| Post Credit Limit | Facility (only shown when not already posted) | Posts the memo entry in §6.3 |
| Cancel Credit Limit Posting | Facility (only shown when already posted) | Cancels the memo JE so it can be re-posted |

---

## 8. Monthly workflow, once everything is set up

1. Desjardins statement arrives.
2. For each Fixed tranche: open it, confirm the next Draft entry (Scheduled) matches the statement,
   then click **Post Next Payment** on the Tranche (or open the entry itself and click **Submit**).
3. For the Revolving Portion: click **Add Manual Payment** on the tranche, fill in the actual payment
   amount, principal, and interest from the statement, and **Submit** it (same JE structure as Fixed —
   it just uses whatever you entered instead of a formula).
4. Open the Facility record — Total Balance and Available Credit are already current from step 2–3;
   click **Refresh Balance** only if you edited a tranche's Current Balance by hand outside this flow.

---

## 9. Submit, Draft, manual entries, and simulation

A HELOC Amortization Entry is a standard Frappe **submittable** document — Draft → Submitted →
Cancelled — and that lifecycle *is* the posting workflow now, not a separate "Posted" checkbox on a
child row.

### 9.1 Draft = simulation, Submit = the only thing that posts

- **Draft.** Fully editable, deletable, and freely creatable. Nothing about a Draft entry has touched
  the GL — no Journal Entry exists for it yet. This means **Generate Schedule doubles as a what-if
  simulation**: it drops the whole schedule onto the Tranche as Draft entries, which immediately feed
  the Tranche's summary stats and charts (§12) and the Facility's rollup/burndown, so you can see the
  full projected payoff curve and total interest cost *before* a single real payment posts. Edit,
  delete, or regenerate freely while everything is Draft.
- **Submitted.** Only Submit creates the Journal Entry (§6.1), and only at that point are the entry's
  figures locked — standard Frappe behavior for submittable docs, no custom code needed for it. If you
  need to correct a submitted entry, Cancel it (see §9.3) and Amend, rather than editing in place.
- **Cancelled.** Cancelling a submitted entry reverses it (§9.3).

### 9.2 Manual entries

**Add Manual Payment** on a Tranche opens a new HELOC Amortization Entry with Entry Type set to
Manual, Tranche and Opening Balance pre-filled from the tranche's current numbers. Use this for:

- Every Revolving Portion payment (it never has Scheduled entries — see §4).
- A one-off or off-schedule payment on a Fixed tranche.
- A backdated or corrected entry, where you may need to tick **Skip Opening Balance Check** (an
  advanced field, only shown when Entry Type is Manual) if the normal opening-balance guardrail below
  would otherwise block it.

A Manual entry posts through the exact same Submit → Journal Entry path as a Scheduled one — there's
no functional difference once it's filled in, only the Entry Type label for your own reference.

### 9.3 Cancelling a posted entry

Cancelling a Submitted HELOC Amortization Entry cancels its linked Journal Entry and rolls the
Tranche's Current Balance back to that entry's Opening Balance — but **only if it's the most recently
posted entry on that tranche** (its Closing Balance still matches the tranche's Current Balance).
Since Current Balance is a running total built by walking forward one submitted entry at a time,
cancelling one from the middle of the posted history would leave that running total ambiguous — so
cancellation has to unwind in the same order postings went in, last-in-first-out. Cancel later entries
first if you need to reverse an earlier one.

### 9.4 Other guardrails

- **Account cross-checks.** Liability/Interest/Bank accounts on a Tranche are validated against that
  Tranche's Company, must be ledger (not Group) accounts, and must be the expected type (Liability /
  Expense / Asset respectively).
- **Opening-balance check.** Submitting an entry refuses to post if its Opening Balance doesn't match
  the tranche's Current Balance — catches a skipped, reordered, or edited entry. Override with
  **Skip Opening Balance Check** only for an intentional correction/back-entry (§9.2).
- **Principal + Interest must equal the Payment Amount.** Enforced on every save, Draft or not —
  otherwise the Journal Entry it would post wouldn't balance.
- **Deletion guards.** A Tranche with any Submitted payments can't be deleted (close it instead); a
  Facility with linked Tranches can't be deleted until those are removed or reassigned. Draft or
  Cancelled entries can be deleted freely (standard Frappe behavior for non-Submitted docs).
- **Closed status is checked.** A Tranche can't be set to Closed while its Current Balance is nonzero
  — catches a tranche being closed out before it's actually paid off.
- **Concurrent posting is locked.** Submitting an entry and Carve Out New Tranche row-lock the
  relevant Tranche for the duration of the action, so two overlapping clicks (or two sessions) can't
  both post against the same balance and double-count.
- **Account link fields are filtered.** Every Account link in the app (Tranche's three accounts, the
  Facility's Credit Limit memo pair, the Group Liability Account reference, and the Carve Out dialog's
  three fields) only shows accounts matching the expected Company and account type — a Liability account
  can't be picked where an Expense account belongs, and so on. The Credit Limit memo pair is the one
  exception: since it's a genuine contra relationship, both sides accept either Asset or Liability type.
  These filters are enforced server-side too, not just in the dropdown.
- **Cancellation from the Journal Entry side.** If a payment's Journal Entry gets cancelled directly
  (outside the entry's own Cancel button), the app automatically cancels the linked HELOC Amortization
  Entry too and reverts the tranche's balance to match — subject to the same last-in-first-out rule in
  §9.3. Carve-out and Credit Limit memo entries can't be cancelled directly at all — Credit Limit memo
  entries must go through **Cancel Credit Limit Posting** on the Facility; carve-out entries aren't
  reversible from within the app once posted, since unwinding one safely isn't always possible if
  payments have already posted against the resulting tranche.

## 10. Budget integration — currently ON HOLD

**Status: disabled.** `sync_budget()` no longer does anything except throw an explanatory error, and
the **Sync Budget** button has been removed from the Facility form. This section documents why, so
the reasoning isn't lost.

### 10.1 What went wrong

The original design assumed a Budget schema that turned out not to match the actual ERPNext version
running here (v16, confirmed against `erpnext/accounts/doctype/budget/budget.py` on the `develop`
branch directly, not a snippet or older docs page):

| Assumed | Actual (confirmed from source) |
|---|---|
| One Budget doc per Cost Center, with a child table listing multiple accounts | One Budget doc = **exactly one account** (`account: DF.Link`) |
| Single `fiscal_year` field | `from_fiscal_year` / `to_fiscal_year` (a range) |
| Any account type allowed | Hard-blocked to Income/Expense accounts only — see below |

### 10.2 The actual blocker

Straight from `Budget.validate_account()`:

```python
elif account_details.report_type != "Profit and Loss":
    frappe.throw(_("Budget cannot be assigned against {0}, as its Root Type is not of Income or Expense"))
```

**A Tranche's Liability Account (the principal side) can never be budgeted via ERPNext's Budget
doctype.** There's no override or config flag — it's a hard validation in ERPNext core. Only
**interest** (an Expense account) could ever go into a native Budget document; principal tracking
would have to live somewhere else entirely (e.g. extending the charts already on the Tranche/Facility
with a manually-set target, rather than ERPNext's Budget module).

### 10.3 What's still in place

- **Cost Center** field on the Facility is still there and still functional — every real payment JE
  (posted when a HELOC Amortization Entry is Submitted) still tags it, so it's usable for filtering in
  ERPNext's own reports even without a formal Budget document.
- `sync_budget()` is left in the codebase as a disabled stub (throws a clear message explaining the
  above) rather than deleted, so the whitelisted method stays documented instead of silently 404ing
  if anything still references it.

Revisit this once there's a clear answer for how (or whether) to represent principal tracking, since
it can't follow the same path as interest.

## 12. Charts

No setup required — these render automatically wherever there's an amortization entry, Draft or
Submitted:

- **On each Tranche:** a Balance Burndown line chart (opening balance through every closing balance)
  and a Principal vs Interest stacked bar chart, one bar per entry, so you can see the split shift
  over the life of the tranche. Draft entries are included, so a freshly generated schedule shows its
  full projected curve immediately, before anything is posted.
- **On the Facility:** a single Total Balance Burndown line chart merging every linked tranche onto
  one timeline — this is a real point-in-time sum (each tranche's balance is held constant between
  its own entry dates), not a naive add-up of mismatched date columns, so it's accurate even when
  tranches have different payment schedules or start dates. Cancelled entries are excluded; Draft and
  Submitted are both included, same as the per-Tranche charts.

## 13. Things to double-check before your first live posting

- Confirm every Liability Account you link is the correct **child** account (2102N / 21010), not the
  21000 or 21020 **group** accounts — ERPNext will generally block postings to group accounts anyway,
  but worth confirming your COA has them correctly typed as Group vs. Ledger.
- Confirm the Interest Expense Account for every Fixed tranche points at the *same* pooled
  `HELOC Interest - Prêt Lié` account, not a per-tranche one.
- Run your first Carve Out or your first real Submit on a staging site if possible, and check the
  resulting Journal Entry against what you'd have entered manually before trusting it on live data.
- Remember Draft entries are a free simulation — use Generate Schedule (or a few Manual entries) on
  staging to sanity-check the numbers before you ever click Submit for real.
