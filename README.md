# Payoff Planner

A desktop app that reads your own bank and credit card statements, works out
what you actually spend, and tells you how to clear your cards — and what to
move out of each paycheck to make it happen.

It runs entirely on your machine. Nothing is uploaded, no account is linked,
no subscription. Point it at a folder of statement PDFs and it does the rest.

![The printable payday plan it produces](docs/payday-plan.png)

---

## Why this exists

Budgeting apps want your bank login. Spreadsheets want you to type everything
in by hand and then quietly go stale. This sits in between: it reads the PDFs
your bank already gives you, and it is honest about what it finds — including
when the answer is that your plan does not add up.

If you are running a deficit, it says so in red on the front page and refuses
to show a payoff date. That is the point.

---

## What it does

**Reads your statements.** Point it at a folder and press one button. It walks
every subfolder, works out which issuer each file is from, and pulls out
transactions, balances, minimums, APRs, credit limits and statement dates.

- Works today with American Express, Apple Card, Credit One and First Community
  Credit Union PDFs, in both old and new statement layouts
- Reads any ordinary bank CSV — single `Amount` column, split debit/credit
  columns, ISO or US dates, semicolons, BOMs, parenthesis negatives, extra
  columns, `.tsv`
- Remembers what it has already read, so scanning again only picks up what is
  new or has changed
- Refuses cleanly on anything it cannot parse, and tells you why

**Keeps balances current, safely.** Import a statement and the balance,
minimum, APR and limit update on their own — but only if that statement is
newer than the date already on the balance. Re-importing an old PDF cannot drag
your numbers backwards. Type a balance in yourself and it is stamped today, so
it stands until a genuinely newer statement arrives.

Two cards from the same bank are matched by account number, not by issuer name.
If it cannot tell them apart it **refuses to guess** and says so.

**Works out what you really spend.** Transactions are categorised
automatically. One-time charges — a holiday, an annual bill, a car title fee —
can be marked so they stop inflating your monthly averages while staying
visible in the ledger. Mark a whole trip by date range in one go.

**Plans the payoff.** Highest-rate-first, with one override: a card on a 0%
promotional rate gets a reserved slice each month so it lands at zero before
the promo expires. If it cannot make that deadline it warns you rather than
quietly missing it.

**Splits your costs across accounts.** Name each debit card or envelope, assign
categories and bills to it, and the app works out what to move on each payday.
A bill can be pinned to a specific paycheck — a car loan due on the 5th comes
out of the first check in full, and the split slider only divides what is left
over. Card payments work the same way.

**Prints a payday plan.** One page, two columns, one per paycheck: every bill
and payment, the amount, why that amount, and what is left afterwards.

**Also covers:** savings goals with funding dates, a home affordability
calculator with adjustable price, down payment, rate and term, and a PMI
section that explains the three ways mortgage insurance ends and shows both
cancellation dates for your numbers.

---

## Screenshots

The payday plan above is generated from the bundled sample data. Everything in
it is fictional.

---

## Install

You need Python 3.9 or newer. On Windows the bundled `.bat` files find it for
you, including the newer install-manager layout that is not on `PATH`.

### Windows

1. Download or clone this repository
2. Double-click **`CHECK PYTHON.bat`** — it reports which Python it found and
   what is missing
3. Double-click **`SETUP.bat`** — installs the PDF readers
4. Double-click **`RUN.bat`**

If no Python is found, get it from [python.org](https://www.python.org/downloads/)
and run `CHECK PYTHON.bat` again.

### macOS / Linux

```bash
git clone https://github.com/YOURNAME/payoff-planner.git
cd payoff-planner
pip install pdfplumber pypdf
python finplan.py
```

### Dependencies

| Package | Needed for |
|---|---|
| `pdfplumber` | reading PDF statements |
| `pypdf` | fallback PDF reader |
| `tkinter` | the window (ships with Python; on Debian/Ubuntu `apt install python3-tk`) |

CSV import needs neither PDF library. The app still runs without them.

---

## Using it

| Command | What it does |
|---|---|
| `python finplan.py` | launch the app |
| `python finplan.py --report` | print the whole plan as text |
| `python finplan.py --selftest` | run the built-in checks |

A fresh copy starts from bundled **sample data** so there is something to look
at. Import your own statements, or edit the figures on the Debts, Bills and
Accounts tabs, to replace it.

### The tabs

| Tab | What lives there |
|---|---|
| **Dashboard** | total debt, free cash, a "worth knowing" panel that flags what actually matters, your own notes, per-account utilisation, and where each paycheck goes |
| **Transactions** | every imported row — searchable, sortable, editable; mark one-offs and whole trips |
| **Spending & budget** | a budget slider per category that goes both ways, a month-by-month trend, a sparkline per category, and which account pays for what |
| **Accounts** | spending accounts, per-paycheck funding, and pinning bills and card payments to a specific check |
| **Savings** | goals with progress and funding dates, warning you if you commit more than you have |
| **Home & PMI** | affordability, payment breakdown, the PMI threshold, and life-of-loan interest |
| **Plan** | the payoff schedule, a chart, what to send where each month, what you actually sent, and what trimming more would buy |
| **Debts / Bills** | balances, APRs, promo dates, credit limits, recurring bills |

---

## Your data stays yours

**Everything lives in one file, `finplan_data.json`, in the app folder.** It
never leaves your computer. There is no account, no sync and no telemetry.

That file also holds every transaction, balance, account-number fragment and
note you have entered.

> ### ⚠️ Do not commit `finplan_data.json`
>
> The included `.gitignore` excludes it, along with statement PDFs, CSVs and
> exported reports. **Leave those rules in place.** Git keeps history forever,
> so deleting the file in a later commit does not remove it — anyone can read
> it from the history.
>
> If you have already pushed one, treat the details in it as exposed: rotate
> what you can and rewrite the history with
> [`git filter-repo`](https://github.com/newren/git-filter-repo) or
> [BFG](https://rtyley.github.io/bfg-repo-cleaner/).

**Saves are crash-safe.** Every write goes to a temporary file, is flushed to
disk, and is swapped in atomically. The previous version is kept as `.bak`. If
the main file is ever unreadable the app loads the backup, tells you what
happened, and sets the damaged copy aside as `.broken` rather than crashing.

---

## How it works

Two files, no framework.

| File | Lines | What it is |
|---|---:|---|
| `core.py` | ~1,900 | statement parsing, categorisation, and all the maths. No UI code — every function is testable on its own |
| `finplan.py` | ~3,200 | the tkinter interface, plus a text report and the self-test |

**Statement detection** scores each format on markers that only appear in its
own letterhead. A checking statement lists card payments as merchants, so a
brand name alone is not enough to identify an issuer — that mistake mislabels a
whole bank statement.

**The payoff engine** accrues interest monthly, pays minimums, reserves for any
promotional-rate card, then sends everything left to the highest rate. It was
verified against an independently written simulator across several debt
profiles — months, total interest and payoff order all agree.

**Categorisation** is a list of regex rules, checked in order. Built-in rules
are versioned so a fix reaches existing data files; rules you add yourself are
kept separately and always win. A category you set by hand is never overwritten.

---

## Testing

```bash
python finplan.py --selftest
```

Covers loading, categorisation, exclusions, cash flow, the payoff engine,
promo deadlines, same-issuer matching, add/remove for every object type,
save/load round-trips of every field, CSV import and error handling.

The maths was additionally checked against independent implementations rather
than by re-running the same code:

- mortgage payments against the closed-form amortization formula
- PMI cancellation months against a hand-rolled amortization schedule
- the payoff engine against a separately written simulator
- invariants: total paid equals principal plus interest, no month overspends,
  balances never go negative, paying more never lengthens the payoff

The interface was exercised headlessly against a tkinter stub across 18 data
states — no debts, no transactions, zero income, rent above income, expired
promos, 100% APR, huge balances — firing every control and event binding.

---

## Limitations

Worth knowing before you rely on it.

- **Statement parsers are format-specific.** The four supported issuers are the
  ones the author has statements for. Others need a parser — see below. CSV
  import works with anything.
- **A statement printed to PDF from a browser cannot be read.** That produces a
  picture of the page with no text in it. Use your bank's own download button,
  or export CSV.
- **Categorisation is rules-based**, not learned. It gets most of it right and
  you fix the rest by hand, which then sticks.
- **It is a planning tool, not advice.** Rates, tax rates and insurance figures
  are defaults you should check. For anything involving a real mortgage,
  a pre-approval is the only number that counts.

---

## Adding your bank

1. Add markers to `detect_type()` in `core.py` — use letterhead text, not
   merchant names
2. Write a `parse_yourbank(text, source)` returning `(list[Txn], meta)` where
   `meta` may carry `balance`, `minimum`, `apr`, `limit`, `statement_date`
   and `last4`
3. Add it to the dispatch in `import_file()`
4. Run `python finplan.py --selftest`

Transactions are positive for money out and negative for money in.

---

## Supporting it

If it saved you an afternoon, there is a Sponsor button at the top of the
repository. Entirely optional — the app is free for any noncommercial use
regardless.

---

## Contributing

Issues and pull requests welcome, particularly new statement parsers. Please
run `--selftest` before opening one, and **never attach a real statement or
data file** — describe the layout, or send a redacted sample with fictional
figures.

By opening a pull request you agree that your contribution is licensed under
the same terms as the rest of the project.

---

## License

**[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)**
— see [LICENSE](LICENSE).

In plain terms:

- **Yes** — use it, change it, share it, fork it, build on it, for anything
  personal, educational, charitable or otherwise noncommercial
- **Yes** — non-profits, schools, research bodies and government use it freely
- **No** — you may not sell it, or use it commercially, without permission

If you want to use it commercially, open an issue and ask.


