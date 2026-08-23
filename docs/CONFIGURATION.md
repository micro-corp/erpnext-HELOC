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
   │       └── HELOC Amortization Entry rows (added manually, no fixed schedule)
   │
   ├── HELOC Tranche  (Fixed — Prêt Lié Tranche 1)
   │       └── HELOC Amortization Entry rows (auto-generated)
   │
   └── HELOC Tranche  (Fixed — Prêt Lié Tranche 2, 3, ...)
           └── HELOC Amortization Entry rows (auto-generated)
```

- **HELOC Facility** is the global record — credit limit, total balance across everything, available
  credit. You'll only have one of these per actual HELOC.
- **HELOC Tranche** is one record per Revolving Portion or per fixed-rate Prêt Lié tranche. Each one
  points at its own GL accounts.
- **HELOC Amortization Entry** rows live inside a Tranche's schedule table — one row per scheduled
  payment. You don't create these directly; they're generated (Fixed tranches) or added manually
  (Revolving).

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

Save. Because this is Revolving, **no Generate Schedule button appears** (see §7). Add
HELOC Amortization Entry rows to the schedule table by hand each time you get a statement.

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

Save, then click **Generate Schedule**. This populates the amortization table using standard
level-payment math (equal payment each month, principal/interest split shifts over time).

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

Two actions post to the GL. Both create a **submitted Journal Entry** — nothing posts silently or on
a schedule; you always click a button.

### 6.1 Post Next Payment (on a Tranche)

Posts the earliest un-posted row in that tranche's schedule.

| Line | Account | Debit | Credit |
|---|---|---|---|
| 1 | Tranche's Interest Expense Account | Interest portion | — |
| 2 | Tranche's Liability Account | Principal portion | — |
| 3 | Tranche's Bank Account | — | Total payment |

This is the same for both Revolving (manually-entered rows) and Fixed tranches.

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
| Generate Schedule | Fixed Tranche only | Builds the amortization table from Principal/Rate/Term/Start Date |
| Post Next Payment | Any Tranche with unposted rows | Posts + submits the JE in §6.1, updates balances |
| Refresh Balance | Facility | Manually recomputes Total Balance / Available Credit |
| Carve Out New Tranche | Facility | Runs the full workflow in §6.2 |
| Post Credit Limit | Facility (only shown when not already posted) | Posts the memo entry in §6.3 |
| Cancel Credit Limit Posting | Facility (only shown when already posted) | Cancels the memo JE so it can be re-posted |

---

## 8. Monthly workflow, once everything is set up

1. Desjardins statement arrives.
2. For each Fixed tranche: open it, confirm the next schedule row matches the statement, click
   **Post Next Payment**.
3. For the Revolving Portion: add/update the HELOC Amortization Entry row for the period based on the
   statement's actual balance and interest charged, then click **Post Next Payment** on that row too
   (same button, same JE structure — it just uses whatever you entered instead of a formula).
4. Open the Facility record — Total Balance and Available Credit are already current from step 2–3;
   click **Refresh Balance** only if you edited a tranche's Current Balance by hand outside this flow.

---

## 9. Data integrity guardrails built into the app

These run automatically — nothing to configure, just worth knowing they're there:

- **Posted rows are locked.** Once a schedule row has a submitted Journal Entry behind it, its date/
  amounts can't be edited or deleted in the grid.
- **Account cross-checks.** Liability/Interest/Bank accounts on a Tranche are validated against that
  Tranche's Company, must be ledger (not Group) accounts, and must be the expected type (Liability /
  Expense / Asset respectively).
- **Opening-balance check.** Post Next Payment refuses to post if the row's Opening Balance doesn't
  match the tranche's current Current Balance — catches skipped or reordered rows.
- **Deletion guards.** A Tranche with any posted payments can't be deleted (close it instead); a
  Facility with linked Tranches can't be deleted until those are removed or reassigned.
- **Closed status is checked.** A Tranche can't be set to Closed while its Current Balance is nonzero
  — catches a tranche being closed out before it's actually paid off.
- **Concurrent posting is locked.** Post Next Payment and Carve Out New Tranche row-lock the relevant
  Tranche for the duration of the action, so two overlapping clicks (or two sessions) can't both post
  against the same balance and double-count.
- **Account link fields are filtered.** Every Account link in the app (Tranche's three accounts, the
  Facility's Credit Limit memo pair, the Group Liability Account reference, and the Carve Out dialog's
  three fields) only shows accounts matching the expected Company and account type — a Liability account
  can't be picked where an Expense account belongs, and so on. The Credit Limit memo pair is the one
  exception: since it's a genuine contra relationship, both sides accept either Asset or Liability type.
  These filters are enforced server-side too, not just in the dropdown.
- **Cancellation handling.** If a payment's Journal Entry gets cancelled directly (outside Post Next
  Payment), the app automatically reverts that row and the tranche's balance to match. Carve-out and
  Credit Limit memo entries can't be cancelled directly at all — Credit Limit memo entries must go
  through **Cancel Credit Limit Posting** on the Facility; carve-out entries aren't reversible from
  within the app once posted, since unwinding one safely isn't always possible if payments have
  already posted against the resulting tranche.

## 10. Budget integration (optional)

ERPNext's native Budget doctype doesn't support budgeting against an account directly — it only
budgets against a **Cost Center** or **Project**. This app uses Cost Center.

### 10.1 Setup

1. Create a dedicated Cost Center for the facility (Accounts → Chart of Cost Centers), e.g.
   `HELOC - Marge Atout`. Doesn't need to be used anywhere else — it exists purely so this facility
   has something to budget against.
2. Set it on the Facility's **Cost Center** field.
3. Once set, a **Sync Budget** button appears on the Facility.

### 10.2 What Sync Budget does

Pick a Fiscal Year and it will:

- Sum every linked tranche's forecasted **interest** (Interest Expense Account) and **principal**
  (Liability Account) for schedule rows falling within that Fiscal Year
- Create (or update/replace) one ERPNext Budget document against your Cost Center, with one row per
  account and that account's annual total as `budget_amount`
- Set `Applicable on Booking Actual Expenses` so ERPNext compares it against real postings, and
  `Warn` (not `Stop`) as the exceeded-budget action, so a legitimate scheduled payment is never
  blocked from posting

Going forward, every **Post Next Payment** Journal Entry tags this Cost Center on its interest and
principal lines automatically, so ERPNext's own **Budget Variance Report** reflects real activity.
The Carve Out Journal Entry is deliberately **not** tagged — it's a balance reclassification between
two liability accounts, not an actual principal payment, and tagging it would misstate the report.

### 10.3 Known limitation

ERPNext applies a single Monthly Distribution curve to every account inside one Budget document, but
interest and principal each have a different monthly shape on an amortizing loan (interest declines
as principal grows). Sync Budget sets accurate **annual totals** per account and leaves monthly
distribution at ERPNext's default (even split) — it does not attempt to fake a precise month-by-month
curve, since a single shared curve genuinely can't represent both shapes correctly at once. If you
want a specific monthly curve, set your own Monthly Distribution on the resulting Budget record
manually; Sync Budget won't overwrite that field if you've already set it.

## 12. Charts

No setup required — these render automatically wherever there's schedule data:

- **On each Tranche:** a Balance Burndown line chart (opening balance through every closing balance)
  and a Principal vs Interest stacked bar chart, one bar per scheduled payment, so you can see the
  split shift over the life of the tranche.
- **On the Facility:** a single Total Balance Burndown line chart merging every linked tranche onto
  one timeline — this is a real point-in-time sum (each tranche's balance is held constant between
  its own payment dates), not a naive add-up of mismatched date columns, so it's accurate even when
  tranches have different payment schedules or start dates.

## 13. Things to double-check before your first live posting

- Confirm every Liability Account you link is the correct **child** account (2102N / 21010), not the
  21000 or 21020 **group** accounts — ERPNext will generally block postings to group accounts anyway,
  but worth confirming your COA has them correctly typed as Group vs. Ledger.
- Confirm the Interest Expense Account for every Fixed tranche points at the *same* pooled
  `HELOC Interest - Prêt Lié` account, not a per-tranche one.
- Run the first Carve Out or Post Next Payment on a staging site if possible, and check the resulting
  Journal Entry against what you'd have entered manually before trusting it on live data.
