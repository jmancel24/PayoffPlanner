#!/usr/bin/env python3
"""
Payoff Planner — a desktop app for Josh's credit card payoff.

    python finplan.py              launch the app
    python finplan.py --selftest   run the logic tests (no window)
    python finplan.py --report     print a text summary

Import your statements with the Import button. AMEX, Apple Card and Credit One
PDFs read directly. Bank statements that are scans (no selectable text) can't be
read from PDF — export CSV from online banking and import that instead.

Everything you change is saved to finplan_data.json next to this file.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core
from core import Store, Debt, Bill, Txn, project, scenarios, spread_cut, month_label

DATA_PATH = os.path.join(HERE, core.DATA_FILE)

M = lambda v: f"${v:,.2f}"
M0 = lambda v: f"${v:,.0f}"

FONT = "Segoe UI"

# Two themes from the same palette. Dark is stepped for a dark surface, not an
# automatic flip of the light values.
THEMES = {
    # Light and dark are separate designs, not one flipped. Both were measured:
    # every text pairing clears WCAG, and adjacent surfaces are ~7 L* apart so
    # panels actually read as panels.
    "light": {
        "bg": "#f4f4f1", "card": "#ffffff", "raised": "#f7f7f4",
        "line": "#dedcd4", "wash": "#eef2f8",
        "ink": "#14140f", "ink2": "#54534e", "muted": "#7d7b74", "axis": "#c3c2b7",
        "good": "#0ca30c", "okink": "#146c2e", "bad": "#b3261e",
        "warn": "#eda100", "warnink": "#8a5a00",
        "hero": "#14140f", "heroink": "#ffffff", "herosub": "#c8c7bd",
        "goodbg": "#eef7ee", "badbg": "#fdf1f0", "warnbg": "#fdf6e8",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
                   "#4a3aa7", "#008300", "#e34948"],
    },
    "dark": {
        "bg": "#1b1a18", "card": "#2b2a28", "raised": "#3a3937",
        "line": "#4f4e4c", "wash": "#33404f",
        "ink": "#f7f6f1", "ink2": "#d2d0c5", "muted": "#a5a299",
        "axis": "#615f5a",
        "good": "#4ad257", "okink": "#7ee089", "bad": "#ff7d72",
        "warn": "#f2b52e", "warnink": "#f5cd6b",
        "hero": "#22344f", "heroink": "#ffffff", "herosub": "#c3d0e2",
        "goodbg": "#1d3524", "badbg": "#3d2320", "warnbg": "#3b2f18",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
                   "#9085e9", "#0ca30c", "#e66767"],
    },
}
C = dict(THEMES["light"])
SERIES = list(C["series"])


def apply_theme(name):
    """Swap every colour in place so existing references stay valid."""
    t = THEMES.get(name, THEMES["light"])
    C.clear()
    C.update(t)
    SERIES[:] = list(t["series"])
    return name


def project_with_cuts(store, cuts):
    """Project under a different set of cuts without disturbing the live ones."""
    keep = dict(store.cuts)
    store.cuts = dict(cuts)
    try:
        return project(store)
    finally:
        store.cuts = keep


# ═══════════════════════════════════════════════════════════════════════════
#  HEADLESS REPORT  (also what --selftest exercises)
# ═══════════════════════════════════════════════════════════════════════════

def report(store: Store) -> str:
    o = []
    w = o.append
    ms = store.baseline_months()
    w("=" * 74)
    w("  PAYOFF TRACKER")
    w("=" * 74)

    w("\nDEBTS")
    tot = ints = 0.0
    for d in store.debts:
        if not d.include:
            continue
        i = d.balance * d.apr / 12
        tot += d.balance
        ints += i
        promo = f"  0% until {d.promo_until}" if d.promo_until else ""
        w(f"  {d.name:<20}{M(d.balance):>12}{d.apr*100:>8.2f}%"
          f"{M(d.minimum):>10} min{M(i):>10}/mo{promo}")
    w(f"  {'TOTAL':<20}{M(tot):>12}{'':>8}{'':>14}{M(ints):>10}/mo in interest")

    w(f"\nSPENDING BASELINE  (averaged over {len(ms)} month(s): {', '.join(ms)})")
    by = store.spend_by_category()
    for k, v in by.items():
        cut = store.cuts.get(k, 0.0)
        tail = f"   cut {M(cut)} -> keep {M(v-cut)}" if cut else ""
        w(f"  {k:<28}{M(v):>11}/mo{tail}")
    w(f"  {'TOTAL':<28}{M(store.spend_total()):>11}/mo")
    ex = store.excluded_total()
    if ex:
        w(f"  ({M(ex)} of one-time charges excluded from the baseline)")

    w(f"\nCASH FLOW")
    w(f"  Income{M(store.income_base()):>44}")
    w(f"  Bills{'-' + M(store.bills_total()):>45}")
    w(f"  Rent{'-' + M(store.settings['rent']):>46}")
    w(f"  Spending{'-' + M(max(0, store.spend_total()-store.cuts_total())):>42}")
    w(f"  {'FREE FOR DEBT':<20}{M(store.free_cash(1)):>30}")

    r = project(store)
    w(f"\nPLAN")
    if r["months"]:
        w(f"  Debt-free {month_label(store.settings['start'], r['months'])} "
          f"({r['months']} months) · {M(r['interest'] or 0)} interest")
    else:
        w("  Balances never clear at this level of spending.")
    for warn in r["warnings"]:
        w(f"  ! {warn}")

    if r["rows"]:
        w("\nSCHEDULE")
        names = [d.name for d in store.debts if d.include]
        w("  " + "Month".ljust(10) + "".join(n[:11].rjust(12) for n in names) + "TOTAL".rjust(12))
        for row in r["rows"][:36]:
            cells = "".join(
                ("—" if row["bal"].get(n, 0) <= 0.005 else M0(row["bal"][n])).rjust(12)
                for n in names)
            w(f"  {row['label']:<10}{cells}"
              f"{(M0(row['total']) if row['total'] > 0.5 else 'DONE').rjust(12)}")

    w("\nSCENARIOS")
    w("  " + "Trim".rjust(7) + "Toward debt".rjust(14) + "Debt-free".rjust(13)
      + "Interest".rjust(12) + "  Promo met")
    for s in scenarios(store):
        w(f"  {M0(s['cut']).rjust(7)}{M0(s['toward_debt']).rjust(14)}"
          f"{s['when'].rjust(13)}"
          f"{(M(s['interest']) if s.get('interest') is not None else '—').rjust(12)}"
          f"   {'yes' if s['ok'] else 'NO'}")
    return "\n".join(o)


# ═══════════════════════════════════════════════════════════════════════════
#  SELF TEST
# ═══════════════════════════════════════════════════════════════════════════

def selftest():
    fails = []

    def check(name, cond, extra=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  <- {extra}"))
        if not cond:
            fails.append(name)

    print("\nSELF TEST\n" + "-" * 60)

    s = Store(DATA_PATH)
    # load_safe falls back to the bundled sample, so this passes on a fresh
    # clone that has no data file yet — which is what a contributor runs first.
    loaded, _msg = s.load_safe()
    s.cuts = {}          # tests always start from an untrimmed baseline
    check("data file loads", loaded, DATA_PATH)
    check("transactions present", len(s.txns) > 100, f"{len(s.txns)}")
    check("debts present", len(s.debts) >= 2, f"{len(s.debts)}")
    check("bills present", len(s.bills) >= 5, f"{len(s.bills)}")

    # categorization
    cats = s.spend_by_category()
    check("categories produced", len(cats) >= 4, f"{len(cats)}")
    unc = cats.get("Uncategorized", 0)
    tot = s.spend_total() or 1
    check("little left uncategorized", abs(unc) / tot < 0.06,
          f"{M(unc)} of {M(tot)}")

    # exclusions
    ex = [t for t in s.txns if t.exclude]
    check("one-time charge excluded", len(ex) >= 1, f"{len(ex)}")
    before = s.spend_total()
    for t in ex:
        t.exclude = False
    after = s.spend_total()
    check("excluding changes the baseline", after > before, f"{M(before)} -> {M(after)}")
    for t in ex:
        t.exclude = True

    # cash flow
    fc = s.free_cash(1)
    check("free cash computes", isinstance(fc, float))
    check("income bump applies later",
          s.income_at(12) > s.income_at(1), f"{s.income_at(1)} vs {s.income_at(12)}")

    # payoff engine
    r0 = project(s)
    check("projection runs", isinstance(r0, dict))
    # size the trim to the actual deficit so the test is data-independent
    s.cuts = spread_cut(s, max(900.0, -s.free_cash(1) + 900.0))
    r1 = project(s)
    check("cuts shorten the payoff",
          r1["months"] and (not r0["months"] or r1["months"] <= r0["months"]),
          f"{r0['months']} -> {r1['months']}")
    # interest is None when a plan never clears — only compare two that do
    check("cuts reduce interest",
          (not r0["months"]) or (r1["interest"] is not None
                                 and r1["interest"] < r0["interest"]),
          f"{r0['interest']} -> {r1['interest']}")

    check("everything reaches zero",
          all(v <= 0.005 for v in r1["rows"][-1]["bal"].values()) if r1["rows"] else False)
    disc = [d for d in s.debts if d.promo_until]
    if disc and r1["months"]:
        endm = core.months_until(s.settings["start"], disc[0].promo_until)
        cl = r1["cleared"].get(disc[0].name, 999)
        check("promo card clears before 0% ends", cl <= endm, f"month {cl} vs {endm}")

    # two cards from the same issuer must never be confused
    probe = Store(DATA_PATH)
    probe.load()
    co = [d for d in probe.debts if "credit one" in d.name.lower()]
    if len(co) >= 2:
        for d in co:
            d.last4 = ""
        check("same-issuer without account numbers refuses to guess",
              probe.match_debt("Credit One") == "ambiguous",
              str(probe.match_debt("Credit One")))
        co[0].last4, co[1].last4 = "4920", "7731"
        check("account number picks the right card",
              probe.match_debt("Credit One", "7731") is co[1])
        check("unknown account number is refused, not guessed",
              probe.match_debt("Credit One", "9999") == "unknown_account")

    # scenarios monotonic
    # Only compare runs that actually reach zero. A scenario that never clears
    # stops simulating early, so its interest figure isn't comparable.
    sc = [x for x in scenarios(s, (600, 900, 1200, 1500)) if x["months"]]
    sc = [x for x in sc if x["interest"] is not None]
    ok = all(sc[i]["interest"] >= sc[i + 1]["interest"] for i in range(len(sc) - 1))
    check("bigger cuts -> less interest", ok and len(sc) >= 2,
          str([(x["cut"], round(x["interest"])) for x in sc]))

    # add / remove
    n = len(s.bills)
    s.bills.append(Bill("Test bill", 50.0, "1st", "Debit"))
    check("can add a bill", s.bills_total() > 0 and len(s.bills) == n + 1)
    s.bills.pop()
    check("can remove a bill", len(s.bills) == n)
    n = len(s.debts)
    s.debts.append(Debt("Test card", 500.0, 0.20, 25.0))
    check("can add a debt", len(s.debts) == n + 1)
    s.debts.pop()

    nt = len(s.txns)
    s.txns.append(Txn("2026-07-15", "TEST MERCHANT", 42.0, "Checking", "Other", "spend"))
    check("can add a transaction", len(s.txns) == nt + 1)
    s.txns.pop()

    # round trip
    tmp = os.path.join(HERE, "_selftest.json")
    s.save(tmp)
    s2 = Store(tmp)
    s2.load()
    check("save/load round-trips",
          len(s2.txns) == len(s.txns) and len(s2.debts) == len(s.debts))
    os.remove(tmp)

    # csv import
    tmpc = os.path.join(HERE, "_t.csv")
    with open(tmpc, "w", encoding="utf-8") as f:
        f.write("Date,Description,Amount\n07/05/2026,TEST COFFEE SHOP,-8.50\n"
                "07/06/2026,PAYROLL DEPOSIT,1982.85\n")
    t, meta = core.parse_csv(tmpc)
    check("CSV import works", len(t) == 2 and t[0].amount == 8.5,
          str([(x.desc, x.amount) for x in t]))
    os.remove(tmpc)

    # scanned pdf gives a helpful error
    msg = ""
    try:
        core.import_file("/nonexistent.pdf")
    except Exception as e:
        msg = str(e)
    check("bad file raises a readable error", len(msg) > 0)

    s.cuts = {}
    print("-" * 60)
    print(f"  {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}\n")
    return 0 if not fails else 1


# ═══════════════════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════════════════

def launch():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox, simpledialog
    except ImportError:
        print("\nTkinter isn't available in this Python install.\n"
              "On Windows, reinstall Python from python.org and make sure\n"
              "'tcl/tk and IDLE' is checked during setup.\n\n"
              "In the meantime, here's the text report:\n")
        s = Store(DATA_PATH)
        s.load()
        print(report(s))
        return 1

    store = Store(DATA_PATH)
    ok, load_msg = store.load_safe()
    start_msg = store.roll_start_forward()
    pruned = store.prune_seen_files()
    apply_theme(store.settings.get("theme", "light"))

    root = tk.Tk()
    root.title("Payoff Planner")
    root.geometry(store.settings.get("window", "1180x780"))
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=23)
        style.configure("Big.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Lab.TLabel", foreground="#666")
        style.configure("Warn.TLabel", foreground="#b3261e")
        style.configure("Good.TLabel", foreground="#146c2e")
    except Exception:
        pass

    status = tk.StringVar(value="Ready")

    # ---- undo -------------------------------------------------------------
    UNDO = []          # snapshots of the whole store, newest last

    def push_undo(label):
        try:
            UNDO.append((label, json.dumps(store.to_dict())))
        except Exception:
            return
        if len(UNDO) > 25:
            UNDO.pop(0)
        try:
            undo_btn.config(state="normal", text=f"Undo {label}"[:22])
        except Exception:
            pass

    def do_undo():
        if not UNDO:
            return
        label, snap = UNDO.pop()
        try:
            d = json.loads(snap)
        except Exception:
            return
        store.txns = [core.Txn(**t) for t in d.get("txns", [])]
        store.debts = [core.Debt(**x) for x in d.get("debts", [])]
        store.bills = [core.Bill(**x) for x in d.get("bills", [])]
        store.goals = [core.Goal(**x) for x in d.get("goals", [])]
        store.cuts = d.get("cuts", {})
        store.settings.update(d.get("settings", {}))
        store.user_rules = [list(r) for r in d.get("user_rules", [])]
        store.rules = store.user_rules + [list(r) for r in core.DEFAULT_RULES]
        save()
        refresh_all()
        status.set(f"Undid: {label}")
        try:
            undo_btn.config(state=("normal" if UNDO else "disabled"),
                            text=(f"Undo {UNDO[-1][0]}"[:22] if UNDO else "Undo"))
        except Exception:
            pass

    def save(*_):
        try:
            store.save(DATA_PATH)
            status.set(f"Saved to {core.DATA_FILE}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def refresh_all():
        for fn in REFRESH:
            try:
                fn()
            except Exception:
                traceback.print_exc()

    REFRESH = []

    def num_control(parent, initial, lo, hi, on_change, fmt=None, step=None,
                    width=10, length=170):
        """
        A slider AND a box you can type in. They stay in sync, so a slider's
        step size never limits you — type the exact number instead.
        Returns (frame, set_value, get_value).
        """
        fmt = fmt or (lambda v: f"{v:,.2f}")
        f = tk.Frame(parent, bg=C["card"])
        var = tk.DoubleVar(value=float(initial))
        ent = ttk.Entry(f, width=width, justify="right")
        guard = {"busy": False}

        def clamp(v):
            try:
                v = float(str(v).replace("$", "").replace(",", "").replace("%", ""))
            except (TypeError, ValueError):
                return None
            return min(max(v, lo), hi)

        def show(v):
            ent.delete(0, "end")
            ent.insert(0, fmt(v))

        def apply(v, from_entry=False):
            if guard["busy"]:
                return
            v = clamp(v)
            if v is None:
                show(var.get())
                return
            guard["busy"] = True
            try:
                var.set(v)
                if not from_entry or True:
                    show(v)
                on_change(v)
            finally:
                guard["busy"] = False

        def bump(d):
            apply(var.get() + d)
            save()

        if step:
            tk.Button(f, text="−", width=2, relief="flat", bg=C["wash"], fg=C["ink"],
                      font=(FONT, 11, "bold"), cursor="hand2",
                      command=lambda: bump(-step)).pack(side="left", padx=(0, 3))
        sc = ttk.Scale(f, from_=lo, to=hi, variable=var, orient="horizontal",
                       length=length, command=lambda v: apply(v))
        sc.pack(side="left")
        sc.bind("<ButtonRelease-1>", lambda e: save())
        if step:
            tk.Button(f, text="+", width=2, relief="flat", bg=C["wash"], fg=C["ink"],
                      font=(FONT, 11, "bold"), cursor="hand2",
                      command=lambda: bump(step)).pack(side="left", padx=(3, 6))
        ent.pack(side="left", padx=(6, 0))
        show(float(initial))

        def commit(_e=None):
            apply(ent.get(), from_entry=True)
            save()
            try:
                ent.selection_range(0, "end")
            except Exception:
                pass
        def nudge(d):
            def go(_e=None):
                apply(var.get() + d)
                save()
                return "break"
            return go
        step_v = step or max((hi - lo) / 100.0, 1)
        ent.bind("<Return>", commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<KP_Enter>", commit)
        ent.bind("<Up>", nudge(step_v))
        ent.bind("<Down>", nudge(-step_v))
        ent.bind("<Prior>", nudge(step_v * 10))     # PageUp
        ent.bind("<Next>", nudge(-step_v * 10))     # PageDown
        sc.bind("<Left>", nudge(-step_v))
        sc.bind("<Right>", nudge(step_v))
        return f, (lambda v: apply(v)), (lambda: var.get())


    # ── toolbar ────────────────────────────────────────────────────────────
    bar = ttk.Frame(root, padding=(10, 8))
    bar.pack(fill="x")

    def do_import():
        paths = filedialog.askopenfilenames(
            title="Import statements",
            filetypes=[("Statements", "*.pdf *.csv *.txt *.tsv"),
                       ("PDF", "*.pdf"), ("CSV", "*.csv"), ("All files", "*.*")])
        if not paths:
            return
        added = dup = 0
        notes, errs, updates, stale = [], [], [], []
        for p in paths:
            try:
                t, meta, msg = core.import_file(p)
                a, d = store.add_txns(t)
                added += a
                dup += d
                base = os.path.basename(p)
                notes.append(f"{base}: {a} new, {d} already had")
                if len(msg.splitlines()) > 1:
                    notes.append("   " + " ".join(msg.split("\n")[2:]))
                # Balances update themselves, quietly, and only from a statement
                # NEWER than whatever the balance is currently true as of.
                r = store.apply_statement(meta)
                if r and not r["skipped"]:
                    updates.append(f"{r['debt']}: {M(r['old'])} → {M(r['new'])}"
                                   f"   (as of {r['asof']})")
                elif r and r.get("ambiguous"):
                    errs.append(f"Balance not updated for {r['debt']}\n\n"
                                f"{r['why']}")
                elif r and r["skipped"]:
                    stale.append(f"{r['debt']}: left at {M(r['old'])} — that "
                                 f"statement ({r['statement']}) is older than "
                                 f"what you already have ({r['asof']})")
            except Exception as e:
                errs.append(f"{os.path.basename(p)}\n{e}")
        save()
        refresh_all()
        if notes:
            body = f"Added {added} transactions ({dup} already had).\n\n" + \
                   "\n".join(notes)
            if updates:
                body += "\n\nBalances updated:\n  " + "\n  ".join(updates)
            if stale:
                body += "\n\nLeft alone:\n  " + "\n  ".join(stale)
            messagebox.showinfo("Import complete", body)
        for e in errs:
            messagebox.showwarning("Could not import", e)

    def scan_root():
        r = store.settings.get("scan_root") or ""
        if r and os.path.isdir(r):
            return r
        # the app usually lives in a subfolder of wherever the statements are
        parent = os.path.dirname(HERE)
        return parent if os.path.isdir(parent) else HERE

    def import_many(paths, skip_seen=True):
        """Import a list of files. Returns a summary string."""
        added = dup = 0
        done, skipped, errs, updates, stale = [], [], [], [], []
        for p in paths:
            if skip_seen and store.file_already_imported(p):
                skipped.append(os.path.basename(p))
                continue
            try:
                t, meta, msg = core.import_file(p)
                a, d = store.add_txns(t)
                added += a
                dup += d
                store.mark_file_imported(p)
                if os.path.basename(p) in store.settings.get("unreadable", []):
                    store.settings["unreadable"].remove(os.path.basename(p))
                done.append(f"{os.path.basename(p)}: {a} new"
                            + (f", {d} already had" if d else ""))
                r = store.apply_statement(meta)
                if r and not r["skipped"]:
                    updates.append(f"{r['debt']}: {M(r['old'])} → {M(r['new'])} "
                                   f"(as of {r['asof']})")
                elif r and r.get("ambiguous"):
                    errs.append(f"{os.path.basename(p)} — balance NOT updated: "
                                f"{r['why']}")
                elif r and r["skipped"]:
                    stale.append(f"{r['debt']}: kept {M(r['old'])}, that statement "
                                 f"({r['statement']}) is older than {r['asof']}")
            except Exception as e:
                errs.append(f"{os.path.basename(p)} — {str(e).splitlines()[0]}")
                bad = store.settings.setdefault("unreadable", [])
                nm = os.path.basename(p)
                if nm not in bad:
                    bad.append(nm)
        save()
        refresh_all()
        out = [f"Added {added} transactions." if added else "No new transactions."]
        if dup:
            out.append(f"{dup} were already in your data.")
        if skipped:
            out.append(f"\n{len(skipped)} file(s) unchanged since last import, "
                       f"so they were skipped.")
        if done:
            out.append("\nRead:\n  " + "\n  ".join(done))
        if updates:
            out.append("\nBalances updated:\n  " + "\n  ".join(updates))
        if stale:
            out.append("\nBalances left alone:\n  " + "\n  ".join(stale))
        if errs:
            out.append("\nCouldn't read:\n  " + "\n  ".join(errs))
        return "\n".join(out)

    def show_text(title, body, w=760, h=520):
        win = tk.Toplevel(root)
        win.title(title)
        win.geometry(f"{w}x{h}")
        sb = ttk.Scrollbar(win, orient="vertical")
        t = tk.Text(win, wrap="word", font=("Consolas", 9), yscrollcommand=sb.set)
        sb.config(command=t.yview)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        t.insert("1.0", body)
        t.config(state="disabled")

    def choose_scan_root():
        d = filedialog.askdirectory(title="Folder to scan for statements",
                                    initialdir=scan_root())
        if d:
            store.settings["scan_root"] = d
            save()
        return d

    def do_scan():
        folder = scan_root()
        files = core.find_statements(folder, skip_dirs=[HERE])
        if not files:
            if messagebox.askyesno(
                    "Nothing found",
                    f"No statement files under:\n{folder}\n\n"
                    "It looks for .pdf, .csv, .tsv and .txt in that folder and "
                    "every folder inside it.\n\nPick a different folder?"):
                if choose_scan_root():
                    do_scan()
            return
        fresh = [p for p in files if not store.file_already_imported(p)]
        seen = len(files) - len(fresh)
        if not fresh:
            messagebox.showinfo(
                "Already up to date",
                f"Found {len(files)} statement file(s) under:\n{folder}\n\n"
                "Every one has already been imported and none have changed "
                "since.")
            return
        listing = "\n".join("   " + os.path.relpath(p, folder) for p in fresh[:25])
        more = f"\n   …and {len(fresh) - 25} more" if len(fresh) > 25 else ""
        if not messagebox.askyesno(
                "Import these?",
                f"Found {len(files)} statement file(s) under:\n{folder}\n\n"
                f"{len(fresh)} not imported yet"
                + (f"   ({seen} already done and unchanged)" if seen else "")
                + ":\n\n" + listing + more + "\n\nImport them now?"):
            return
        show_text("Scan complete", import_many(fresh))

    ttk.Button(bar, text="Import statements…", command=do_import).pack(side="left")
    ttk.Button(bar, text="Scan folder for statements",
               command=do_scan).pack(side="left", padx=6)
    ttk.Button(bar, text="Set scan folder…",
               command=choose_scan_root).pack(side="left")
    ttk.Button(bar, text="Save", command=save).pack(side="left", padx=6)

    def do_report():
        win = tk.Toplevel(root)
        win.title("Text report")
        win.geometry("900x640")
        t = tk.Text(win, wrap="none", font=("Consolas", 9))
        t.pack(fill="both", expand=True)
        t.insert("1.0", report(store))
        t.config(state="disabled")

    ttk.Button(bar, text="Text report", command=do_report).pack(side="left")

    def do_export():
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv")],
                                         initialfile="payoff_schedule.csv")
        if not p:
            return
        import csv as _csv
        r = project(store)
        names = [d.name for d in store.debts if d.include]
        with open(p, "w", newline="", encoding="utf-8") as f:
            wr = _csv.writer(f)
            wr.writerow(["Month"] + [f"{n} balance" for n in names] + ["Total"]
                        + [f"{n} payment" for n in names])
            for row in r["rows"]:
                wr.writerow([row["label"]]
                            + [round(max(row["bal"].get(n, 0), 0), 2) for n in names]
                            + [round(row["total"], 2)]
                            + [round(row["paid"].get(n, 0), 2) for n in names])
        status.set(f"Exported {os.path.basename(p)}")

    ttk.Button(bar, text="Export schedule…", command=do_export).pack(side="left", padx=6)

    def do_payday():
        default = "payday plan.html"
        p2 = filedialog.asksaveasfilename(
            defaultextension=".html", filetypes=[("Web page", "*.html")],
            initialfile=default, initialdir=HERE,
            title="Save your payday plan")
        if not p2:
            return
        try:
            with open(p2, "w", encoding="utf-8") as fh:
                fh.write(core.payday_plan_html(store, "Payday plan"))
        except Exception as e:
            messagebox.showerror("Couldn't save", str(e))
            return
        status.set(f"Saved {os.path.basename(p2)}")
        if messagebox.askyesno(
                "Payday plan saved",
                f"Saved as:\n{p2}\n\nOpen it now?\n\n"
                "It prints cleanly — two columns, one per paycheck."):
            try:
                import webbrowser
                webbrowser.open("file://" + os.path.abspath(p2))
            except Exception:
                pass

    ttk.Button(bar, text="Payday plan…", command=do_payday).pack(side="left")
    def toggle_theme():
        nxt = "dark" if store.settings.get("theme", "light") == "light" else "light"
        store.settings["theme"] = nxt
        save()
        messagebox.showinfo(
            "Theme changed",
            f"Switched to {nxt} mode.\n\nClose and reopen the app to see it "
            "everywhere — colours are baked in when each panel is drawn.")

    ttk.Label(bar, textvariable=status, style="Lab.TLabel").pack(side="right")
    ttk.Button(bar, text="Light / dark", command=toggle_theme).pack(side="right", padx=8)
    undo_btn = ttk.Button(bar, text="Undo", command=do_undo, state="disabled")
    undo_btn.pack(side="right")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=(0, 6))

    # ═══ DASHBOARD ════════════════════════════════════════════════════════
    tab_dash = ttk.Frame(nb, padding=0)
    nb.add(tab_dash, text="  Dashboard  ")

    d_scroll = tk.Canvas(tab_dash, bg=C["bg"], highlightthickness=0)
    d_sb = ttk.Scrollbar(tab_dash, orient="vertical", command=d_scroll.yview)
    d_body = tk.Frame(d_scroll, bg=C["bg"])
    d_body.bind("<Configure>",
                lambda e: d_scroll.configure(scrollregion=d_scroll.bbox("all")))
    d_win = d_scroll.create_window((0, 0), window=d_body, anchor="nw")
    d_scroll.bind("<Configure>", lambda e: d_scroll.itemconfig(d_win, width=e.width))
    d_scroll.configure(yscrollcommand=d_sb.set)
    d_scroll.pack(side="left", fill="both", expand=True)
    d_sb.pack(side="right", fill="y")

    def add_note():
        txt = simpledialog.askstring("Add a note",
                                     "Something to remember every time you open this:")
        if txt and txt.strip():
            push_undo("add a note")
            store.settings.setdefault("notes", []).append(txt.strip())
            save()
            refresh_dash()

    def edit_note(i):
        notes = store.settings.get("notes", [])
        if not (0 <= i < len(notes)):
            return
        cur = notes[i]
        txt = simpledialog.askstring("Edit note", "Note:", initialvalue=cur)
        if txt is not None and txt.strip():
            push_undo("edit a note")
            store.settings["notes"][i] = txt.strip()
            save()
            refresh_dash()

    def del_note(i):
        notes = store.settings.get("notes", [])
        if not (0 <= i < len(notes)):
            return
        push_undo("delete a note")
        notes.pop(i)
        save()
        refresh_dash()

    def edit_money(key, label, then=None):
        cur = store.settings.get(key, 0)
        v = simpledialog.askstring(label, f"{label}:", initialvalue=f"{cur:.2f}")
        if v is None:
            return
        try:
            store.settings[key] = float(str(v).replace("$", "").replace(",", ""))
        except ValueError:
            messagebox.showerror("Not a number", f"Couldn't read '{v}'.")
            return
        save()
        refresh_all()

    def refresh_dash():
        for w in d_body.winfo_children():
            w.destroy()
        r = project(store)
        live = [d for d in store.debts if d.include]
        tot = sum(d.balance for d in live)
        ints = sum(d.balance * d.apr / 12 for d in live)
        fc = store.free_cash(1)

        # ── hero strip ────────────────────────────────────────────────────
        hero = tk.Frame(d_body, bg=C["hero"])
        hero.pack(fill="x")
        inner = tk.Frame(hero, bg=C["hero"])
        inner.pack(fill="x", padx=20, pady=16)
        tk.Label(inner, text="TOTAL CREDIT CARD DEBT", bg=C["hero"], fg=C["herosub"],
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(inner, text=M0(tot), bg=C["hero"], fg=C["heroink"],
                 font=(FONT, 34, "bold")).pack(anchor="w")
        sub = (f"costing you {M(ints)} a month in interest" if ints else "")
        if r["months"]:
            sub += f"   ·   debt-free {month_label(store.settings['start'], r['months'])}"
        else:
            sub += "   ·   no payoff date at your current spending"
        tk.Label(inner, text=sub, bg=C["hero"], fg=C["herosub"],
                 font=(FONT, 10)).pack(anchor="w", pady=(2, 0))

        # ── stat tiles, each with a colored top rule ──────────────────────
        tiles = tk.Frame(d_body, bg=C["bg"])
        tiles.pack(fill="x", padx=14, pady=14)
        stats = [
            ("FREE EACH MONTH", M0(fc), "after bills, rent, spending",
             C["good"] if fc > 200 else (C["warn"] if fc > 0 else C["bad"])),
            ("GOING TO CARDS", M0(max(fc, 0)) + "/mo",
             f"{M0(max(store.free_cash(12), 0))}/mo once the 401(k) loan ends", SERIES[0]),
            ("INTEREST AHEAD",
             M0(r["interest"]) if (r["months"] and r["interest"] is not None) else "—",
             "on the current plan", SERIES[1]),
            ("YOU'RE TRIMMING", M0(store.cuts_total()) if store.cuts_total() else "$0",
             f"of {M0(store.spend_total())} everyday spending", SERIES[2]),
        ]
        for i, (lab, val, note, col) in enumerate(stats):
            c = tk.Frame(tiles, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"])
            c.grid(row=0, column=i, sticky="nsew", padx=5)
            tiles.columnconfigure(i, weight=1)
            tk.Frame(c, bg=col, height=4).pack(fill="x")
            tk.Label(c, text=lab, bg=C["card"], fg=C["muted"],
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 0))
            tk.Label(c, text=val, bg=C["card"], fg=C["ink"],
                     font=(FONT, 21, "bold")).pack(anchor="w", padx=14)
            tk.Label(c, text=note, bg=C["card"], fg=C["ink2"], font=(FONT, 8),
                     wraplength=200, justify="left").pack(anchor="w", padx=14, pady=(0, 12))

        # ── worth knowing ─────────────────────────────────────────────────
        al = core.alerts(store)
        if al:
            box = tk.Frame(d_body, bg=C["bg"])
            box.pack(fill="x", padx=14, pady=(0, 8))
            tk.Label(box, text="WORTH KNOWING", bg=C["bg"], fg=C["muted"],
                     font=(FONT, 8, "bold")).pack(anchor="w", pady=(0, 6))
            tone = {"bad": (C["bad"], C["badbg"]), "warn": (C["warnink"], C["warnbg"]),
                    "good": (C["okink"], C["goodbg"])}
            for lvl, head_, detail in al:
                fgc, bgc = tone.get(lvl, (C["ink2"], C["card"]))
                card = tk.Frame(box, bg=bgc, highlightthickness=1,
                                highlightbackground=fgc)
                card.pack(fill="x", pady=3)
                tk.Frame(card, bg=fgc, width=5).pack(side="left", fill="y")
                inn = tk.Frame(card, bg=bgc)
                inn.pack(side="left", fill="x", expand=True, padx=12, pady=9)
                tk.Label(inn, text=head_, bg=bgc, fg=fgc,
                         font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
                tk.Label(inn, text=detail, bg=bgc, fg=C["ink2"], font=(FONT, 9),
                         wraplength=820, justify="left", anchor="w").pack(fill="x")

        # ── your notes ────────────────────────────────────────────────────
        nb_ = tk.Frame(d_body, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
        nb_.pack(fill="x", padx=14, pady=8)
        nh = tk.Frame(nb_, bg=C["card"])
        nh.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(nh, text="YOUR NOTES", bg=C["card"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(side="left")
        ttk.Button(nh, text="Add a note…", command=add_note).pack(side="right")
        notes = store.settings.get("notes", [])
        if not notes:
            tk.Label(nb_, text="Nothing yet — add reminders like \"rent goes up in "
                               "January\" or \"ask about the reimbursement\".",
                     bg=C["card"], fg=C["muted"], font=(FONT, 9)).pack(
                         anchor="w", padx=14, pady=(0, 12))
        for i, n in enumerate(notes):
            row = tk.Frame(nb_, bg=C["card"])
            row.pack(fill="x", padx=14, pady=2)
            tk.Frame(row, bg=SERIES[i % len(SERIES)], width=4, height=18).pack(
                side="left", padx=(0, 8))
            tk.Label(row, text=n, bg=C["card"], fg=C["ink"], font=(FONT, 9),
                     wraplength=760, justify="left", anchor="w").pack(side="left")
            tk.Button(row, text="✕", relief="flat", bg=C["card"], fg=C["muted"],
                      font=(FONT, 8), cursor="hand2",
                      command=lambda k=i: del_note(k)).pack(side="right")
            tk.Button(row, text="edit", relief="flat", bg=C["card"], fg=SERIES[0],
                      font=(FONT, 8, "bold"), cursor="hand2",
                      command=lambda k=i: edit_note(k)).pack(side="right", padx=6)
        tk.Frame(nb_, bg=C["card"], height=8).pack()

        # ── accounts, with utilization bars ───────────────────────────────
        ab = tk.Frame(d_body, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"])
        ab.pack(fill="x", padx=14, pady=8)
        ah = tk.Frame(ab, bg=C["card"])
        ah.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(ah, text="ACCOUNTS", bg=C["card"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(side="left")
        tk.Label(ah, text="edit these on the Debts tab", bg=C["card"], fg=C["muted"],
                 font=(FONT, 8)).pack(side="right")
        for i, d in enumerate(sorted(live, key=lambda x: -x.apr)):
            row = tk.Frame(ab, bg=C["card"])
            row.pack(fill="x", padx=14, pady=3)
            tk.Frame(row, bg=SERIES[i % len(SERIES)], width=5, height=30).pack(
                side="left", padx=(0, 10))
            left = tk.Frame(row, bg=C["card"])
            left.pack(side="left", fill="x", expand=True)
            l1 = tk.Frame(left, bg=C["card"])
            l1.pack(fill="x")
            tk.Label(l1, text=d.name, bg=C["card"], fg=C["ink"],
                     font=(FONT, 10, "bold")).pack(side="left")
            if d.promo_until:
                chip = tk.Label(l1, text=f" 0% until {d.promo_until} ", bg=C["wash"],
                                fg=SERIES[0], font=(FONT, 8, "bold"))
                chip.pack(side="left", padx=8)
            tk.Label(l1, text=M(d.balance), bg=C["card"], fg=C["ink"],
                     font=(FONT, 11, "bold")).pack(side="right")
            l2 = tk.Frame(left, bg=C["card"])
            l2.pack(fill="x")
            apr_col = C["bad"] if d.apr >= 0.25 else (
                C["warnink"] if d.apr >= 0.10 else C["okink"])
            tk.Label(l2, text=f"{d.apr*100:.2f}% APR", bg=C["card"], fg=apr_col,
                     font=(FONT, 9, "bold")).pack(side="left")
            tk.Label(l2, text=f"   min {M(d.minimum)}", bg=C["card"], fg=C["muted"],
                     font=(FONT, 9)).pack(side="left")
            per = d.balance * d.apr / 12
            tk.Label(l2, text=(f"{M(per)}/mo in interest" if per > 0.005
                               else "costs nothing right now"),
                     bg=C["card"], fg=C["muted"], font=(FONT, 9)).pack(side="right")
            if d.limit:
                pct = min(d.balance / d.limit, 1.0)
                bar = tk.Frame(left, bg=C["line"], height=6)
                bar.pack(fill="x", pady=(4, 0))
                fillw = tk.Frame(bar, bg=C["bad"] if pct > 0.5 else SERIES[0],
                                 height=6, width=max(int(pct * 600), 3))
                fillw.pack(side="left")
                tk.Label(left, text=f"{pct*100:.0f}% of the {M0(d.limit)} limit",
                         bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w")
        tk.Frame(ab, bg=C["card"], height=10).pack()

        # ── cash flow, with a stacked bar ─────────────────────────────────
        cfb = tk.Frame(d_body, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
        cfb.pack(fill="x", padx=14, pady=(8, 20))
        ch_ = tk.Frame(cfb, bg=C["card"])
        ch_.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(ch_, text="WHERE EVERY PAYCHECK GOES", bg=C["card"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(side="left")

        inc = store.income_base()
        spend = store.planned_spend()
        parts = [("Bills", store.bills_total(), SERIES[1]),
                 ("Rent", store.settings["rent"], SERIES[3]),
                 ("Spending", spend, SERIES[4]),
                 ("To debt", max(fc, 0.0), SERIES[2])]
        barw = tk.Frame(cfb, bg=C["card"])
        barw.pack(fill="x", padx=14, pady=(6, 2))
        strip = tk.Frame(barw, bg=C["line"], height=26)
        strip.pack(fill="x")
        denom = max(inc, sum(p[1] for p in parts), 1)
        for nm, v, col in parts:
            if v <= 0:
                continue
            seg = tk.Frame(strip, bg=col, height=26,
                           width=max(int(v / denom * 820), 2))
            seg.pack(side="left")
        if fc < 0:
            tk.Label(barw, text=f"You are {M(abs(fc))} over your income each month.",
                     bg=C["card"], fg=C["bad"], font=(FONT, 9, "bold")).pack(
                         anchor="w", pady=(6, 0))

        grid = tk.Frame(cfb, bg=C["card"])
        grid.pack(fill="x", padx=14, pady=8)
        rows_ = [("Take-home", inc, None, "paycheck"),
                 ("Fixed bills", -store.bills_total(), None, None),
                 ("Rent", -store.settings["rent"], None, "rent"),
                 ("Everyday spending", -spend, None, None),
                 ("FREE FOR DEBT", fc, True, None)]
        for i, (lab, val, bold, editkey) in enumerate(rows_):
            rr = tk.Frame(grid, bg=C["card"])
            rr.pack(fill="x", pady=1)
            for nm, v, col in parts:
                if nm.lower().startswith(lab.split()[0].lower()[:4]):
                    tk.Frame(rr, bg=col, width=10, height=10).pack(side="left", padx=(0, 6))
                    break
            else:
                tk.Frame(rr, bg=C["card"], width=16, height=10).pack(side="left")
            fnt = (FONT, 10, "bold") if bold else (FONT, 10)
            fgc = (C["good"] if val > 0 else C["bad"]) if bold else C["ink"]
            tk.Label(rr, text=lab, bg=C["card"], fg=fgc, font=fnt,
                     anchor="w", width=26).pack(side="left")
            tk.Label(rr, text=M(val), bg=C["card"], fg=fgc, font=fnt,
                     anchor="e", width=14).pack(side="left")
            if editkey:
                tk.Button(rr, text="change", relief="flat", bg=C["wash"], fg=SERIES[0],
                          font=(FONT, 8, "bold"), cursor="hand2", padx=8,
                          command=lambda k=editkey, l=lab: edit_money(k, l)).pack(
                              side="left", padx=10)
        tk.Label(cfb, text="Rent and your paycheck are editable right here — "
                           "use 'change' if rent goes up.",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(
                     anchor="w", padx=14, pady=(0, 12))

    REFRESH.append(refresh_dash)

    # ═══ TRANSACTIONS ═════════════════════════════════════════════════════
    tab_tx = ttk.Frame(nb, padding=10)
    nb.add(tab_tx, text="  Transactions  ")

    txbar = ttk.Frame(tab_tx)
    txbar.pack(fill="x", pady=(0, 6))
    ttk.Label(txbar, text="Search").pack(side="left")
    q = tk.StringVar()
    ttk.Entry(txbar, textvariable=q, width=26).pack(side="left", padx=6)
    fcat = tk.StringVar(value="All categories")
    cb_cat = ttk.Combobox(txbar, textvariable=fcat, width=24, state="readonly")
    cb_cat.pack(side="left", padx=6)
    facct = tk.StringVar(value="All accounts")
    cb_acct = ttk.Combobox(txbar, textvariable=facct, width=16, state="readonly")
    cb_acct.pack(side="left")

    cols = ("date", "account", "desc", "amount", "category", "kind", "one")
    tx_tree = ttk.Treeview(tab_tx, columns=cols, show="headings", selectmode="extended")
    for c, t, wd, an in (("date", "Date", 90, "w"), ("account", "Account", 100, "w"),
                         ("desc", "Description", 400, "w"), ("amount", "Amount", 90, "e"),
                         ("category", "Category", 170, "w"), ("kind", "Type", 100, "w"),
                         ("one", "One-time", 75, "center")):
        tx_tree.heading(c, text=t, command=lambda cc=c: sort_by(cc))
        tx_tree.column(c, width=wd, anchor=an)
    vs = ttk.Scrollbar(tab_tx, orient="vertical", command=tx_tree.yview)
    tx_tree.configure(yscroll=vs.set)
    tx_tree.pack(side="left", fill="both", expand=True)
    vs.pack(side="left", fill="y")

    tx_index = {}
    tx_sort = {"col": "date", "rev": True}

    def sort_by(col):
        if tx_sort["col"] == col:
            tx_sort["rev"] = not tx_sort["rev"]
        else:
            tx_sort["col"], tx_sort["rev"] = col, col in ("date", "amount")
        refresh_tx()

    def visible_txns():
        s = q.get().strip().lower()
        c = fcat.get()
        a = facct.get()
        out = []
        for t in store.txns:
            if s and s not in t.desc.lower():
                continue
            if c != "All categories" and t.category != c:
                continue
            if a != "All accounts" and t.account != a:
                continue
            out.append(t)
        return out

    def refresh_tx(*_):
        for i in tx_tree.get_children():
            tx_tree.delete(i)
        tx_index.clear()
        cats = sorted({t.category for t in store.txns})
        cb_cat["values"] = ["All categories"] + cats
        cb_acct["values"] = ["All accounts"] + sorted({t.account for t in store.txns})
        keyf = {"date": lambda x: x.date, "account": lambda x: x.account,
                "desc": lambda x: x.desc.lower(), "amount": lambda x: x.amount,
                "category": lambda x: x.category, "kind": lambda x: x.kind,
                "one": lambda x: x.exclude}.get(tx_sort["col"], lambda x: x.date)
        for c2, t2, _w, _a in (("date", "Date", 0, 0), ("account", "Account", 0, 0),
                               ("desc", "Description", 0, 0), ("amount", "Amount", 0, 0),
                               ("category", "Category", 0, 0), ("kind", "Type", 0, 0),
                               ("one", "One-time", 0, 0)):
            arrow = ("  ▼" if tx_sort["rev"] else "  ▲") if tx_sort["col"] == c2 else ""
            tx_tree.heading(c2, text=t2 + arrow)
        for t in sorted(visible_txns(), key=keyf, reverse=tx_sort["rev"]):
            iid = tx_tree.insert("", "end", values=(
                t.date, t.account, t.desc[:80], M(t.amount), t.category,
                t.kind, "yes" if t.exclude else ""))
            tx_index[iid] = t
        status.set(f"{len(tx_index)} transactions shown, {len(store.txns)} total")

    q.trace_add("write", lambda *a: refresh_tx())
    cb_cat.bind("<<ComboboxSelected>>", refresh_tx)
    cb_acct.bind("<<ComboboxSelected>>", refresh_tx)
    REFRESH.append(refresh_tx)

    def sel_txns():
        return [tx_index[i] for i in tx_tree.selection() if i in tx_index]

    def tx_set_cat():
        ts = sel_txns()
        if not ts:
            return
        allcats = sorted(set(store.categories) | {t.category for t in store.txns})
        win = tk.Toplevel(root)
        win.title("Set category")
        win.geometry("330x130")
        ttk.Label(win, text=f"Category for {len(ts)} transaction(s):").pack(pady=8)
        v = tk.StringVar(value=ts[0].category)
        cb = ttk.Combobox(win, textvariable=v, values=allcats, width=34)
        cb.pack(pady=4)

        def ok():
            push_undo("category change")
            for t in ts:
                t.category = v.get().strip() or t.category
                t.manual = True
            if v.get().strip() and v.get().strip() not in store.categories:
                store.categories.append(v.get().strip())
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Apply", command=ok).pack(pady=8)

    def tx_set_kind():
        ts = sel_txns()
        if not ts:
            return
        win = tk.Toplevel(root)
        win.title("Set type")
        win.geometry("330x130")
        ttk.Label(win, text="spend = counts in your monthly baseline\n"
                            "bill / debt_payment / income / fee = doesn't").pack(pady=6)
        v = tk.StringVar(value=ts[0].kind)
        ttk.Combobox(win, textvariable=v, state="readonly", width=22,
                     values=["spend", "bill", "debt_payment", "income",
                             "transfer", "fee"]).pack()

        def ok():
            push_undo("type change")
            for t in ts:
                t.kind = v.get()
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Apply", command=ok).pack(pady=8)

    def tx_toggle_one():
        ts = sel_txns()
        if not ts:
            return
        push_undo("one-time toggle")
        newv = not ts[0].exclude
        for t in ts:
            t.exclude = newv
        save()
        refresh_all()

    def tx_delete():
        ts = sel_txns()
        if not ts or not messagebox.askyesno("Delete", f"Delete {len(ts)} transaction(s)?"):
            return
        push_undo("delete")
        keep = {id(t) for t in ts}
        store.txns = [t for t in store.txns if id(t) not in keep]
        save()
        refresh_all()

    def tx_trip():
        win = tk.Toplevel(root)
        win.title("Mark a trip")
        win.configure(bg=C["bg"])
        tk.Label(win, text="Everything you spent between these dates gets marked\n"
                           "one-time, so a holiday stops inflating your monthly\n"
                           "averages. It stays visible in this list.",
                 bg=C["bg"], fg=C["ink2"], font=(FONT, 9), justify="left").pack(
                     padx=14, pady=(14, 8), anchor="w")
        es = {}
        for lab, val in (("Label", "Trip"), ("Start (YYYY-MM-DD)", ""),
                         ("End (YYYY-MM-DD)", "")):
            r = tk.Frame(win, bg=C["bg"])
            r.pack(fill="x", padx=14, pady=4)
            tk.Label(r, text=lab, bg=C["bg"], fg=C["ink"], font=(FONT, 9),
                     width=18, anchor="w").pack(side="left")
            e = ttk.Entry(r, width=20)
            e.insert(0, val)
            e.pack(side="left")
            es[lab] = e

        def ok():
            a = es["Start (YYYY-MM-DD)"].get().strip()
            b = es["End (YYYY-MM-DD)"].get().strip()
            if not re.match(r"\d{4}-\d\d-\d\d", a or "") or \
               not re.match(r"\d{4}-\d\d-\d\d", b or ""):
                messagebox.showerror("Dates", "Use YYYY-MM-DD for both dates.")
                return
            push_undo("mark a trip")
            n = store.mark_trip(a, b, es["Label"].get().strip() or "Trip")
            win.destroy()
            save()
            refresh_all()
            messagebox.showinfo("Marked", f"{n} transaction(s) marked one-time.")
        ttk.Button(win, text="Mark them", command=ok).pack(pady=12)

    def tx_add():
        win = tk.Toplevel(root)
        win.title("Add transaction")
        win.geometry("400x300")
        fields = {}
        for i, (lab, default) in enumerate([
                ("Date (YYYY-MM-DD)", core.date.today().isoformat()),
                ("Description", ""), ("Amount (out = positive)", "0.00"),
                ("Account", "Checking"), ("Category", "Other")]):
            ttk.Label(win, text=lab).grid(row=i, column=0, sticky="w", padx=10, pady=6)
            e = ttk.Entry(win, width=26)
            e.insert(0, default)
            e.grid(row=i, column=1, padx=10)
            fields[lab] = e
        kind = tk.StringVar(value="spend")
        ttk.Label(win, text="Type").grid(row=5, column=0, sticky="w", padx=10)
        ttk.Combobox(win, textvariable=kind, state="readonly", width=24,
                     values=["spend", "bill", "debt_payment", "income", "transfer", "fee"]
                     ).grid(row=5, column=1, padx=10)

        def ok():
            try:
                amt = float(fields["Amount (out = positive)"].get().replace("$", "").replace(",", ""))
            except ValueError:
                messagebox.showerror("Bad amount", "Enter a number.")
                return
            store.txns.append(Txn(
                fields["Date (YYYY-MM-DD)"].get().strip(),
                fields["Description"].get().strip() or "(no description)",
                amt, fields["Account"].get().strip() or "Checking",
                fields["Category"].get().strip() or "Other", kind.get(), source="manual"))
            store.txns.sort(key=lambda t: t.date)
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Add", command=ok).grid(row=6, column=1, pady=14, sticky="e", padx=10)

    txbtn = ttk.Frame(tab_tx)
    txbtn.pack(side="left", fill="y", padx=(8, 0))
    for lab, fn in (("Add…", tx_add), ("Mark a trip…", tx_trip),
                    ("Set category…", tx_set_cat),
                    ("Set type…", tx_set_kind), ("Toggle one-time", tx_toggle_one),
                    ("Delete", tx_delete)):
        ttk.Button(txbtn, text=lab, command=fn, width=17).pack(pady=3)
    ttk.Label(txbtn, text="\nOne-time charges stay\nvisible but are left out\n"
                          "of the monthly average.", style="Lab.TLabel",
              justify="left").pack(pady=8)

    # ═══ SPENDING & CUTS ══════════════════════════════════════════════════
    tab_sp = ttk.Frame(nb, padding=0)
    nb.add(tab_sp, text="  Spending & budget  ")

    sp_top = tk.Frame(tab_sp, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"])
    sp_top.pack(fill="x", padx=12, pady=(12, 6))
    tk.Frame(sp_top, bg=SERIES[0], height=4).pack(fill="x")
    sp_inner = tk.Frame(sp_top, bg=C["card"])
    sp_inner.pack(fill="x")
    sp_now = tk.Label(sp_inner, text="", bg=C["card"], fg=C["ink"],
                      font=(FONT, 16, "bold"))
    sp_now.pack(side="left", padx=14, pady=(10, 2))
    sp_sub = tk.Label(sp_inner, text="", bg=C["card"], fg=C["muted"], font=(FONT, 9))
    sp_sub.pack(side="left")

    def auto_cut():
        v = simpledialog.askstring("Spread a trim",
                                   "Total monthly dollars to trim:", initialvalue="500")
        try:
            v = float((v or "0").replace("$", "").replace(",", ""))
        except ValueError:
            return
        push_undo("spread a trim")
        store.cuts = spread_cut(store, v)
        save()
        refresh_all()

    def pick_months():
        win = tk.Toplevel(root)
        win.title("Months to average")
        win.configure(bg=C["bg"])
        tk.Label(win, text="Which months count toward your averages?", bg=C["bg"],
                 fg=C["ink"], font=(FONT, 10, "bold")).pack(padx=14, pady=(14, 8),
                                                            anchor="w")
        cur = set(store.baseline_months())
        vars_ = {}
        for m in store.months():
            v = tk.BooleanVar(value=m in cur)
            n = sum(1 for t in store.txns if t.date[:7] == m)
            ttk.Checkbutton(win, text=f"{m}   ({n} transactions)",
                            variable=v).pack(anchor="w", padx=20, pady=2)
            vars_[m] = v
        tk.Label(win, text="Untick a partial month so it doesn't drag\nyour averages down.",
                 bg=C["bg"], fg=C["muted"], font=(FONT, 8), justify="left").pack(
                     padx=20, pady=(8, 0), anchor="w")

        def ok():
            store.settings["baseline_months"] = [m for m, v in vars_.items() if v.get()]
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Apply", command=ok).pack(pady=14)

    for lab, fn in (("Months…", pick_months), ("Spread a trim…", auto_cut),
                    ("Reset to baseline",
                     lambda: (push_undo("reset budgets"), store.cuts.clear(),
                              save(), refresh_all()))):
        ttk.Button(sp_inner, text=lab, command=fn).pack(side="right", padx=5, pady=10)

    sp_body = tk.Frame(tab_sp, bg=C["bg"])
    sp_body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    sp_left = tk.Frame(sp_body, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
    sp_left.pack(side="left", fill="both", expand=True)
    sp_canvas = tk.Canvas(sp_left, bg=C["card"], highlightthickness=0)
    sp_scroll = ttk.Scrollbar(sp_left, orient="vertical", command=sp_canvas.yview)
    sp_rows = tk.Frame(sp_canvas, bg=C["card"])
    sp_rows.bind("<Configure>",
                 lambda e: sp_canvas.configure(scrollregion=sp_canvas.bbox("all")))
    sp_win = sp_canvas.create_window((0, 0), window=sp_rows, anchor="nw")
    sp_canvas.bind("<Configure>", lambda e: sp_canvas.itemconfig(sp_win, width=e.width))
    sp_canvas.configure(yscrollcommand=sp_scroll.set)
    sp_canvas.pack(side="left", fill="both", expand=True)
    sp_scroll.pack(side="right", fill="y")

    sp_right = tk.Frame(sp_body, bg=C["card"], width=270, highlightthickness=1,
                        highlightbackground=C["line"])
    sp_right.pack(side="left", fill="y", padx=(10, 0))
    sp_right.pack_propagate(False)
    tk.Frame(sp_right, bg=SERIES[2], height=4).pack(fill="x")
    det_title = tk.Label(sp_right, text="Click a category", bg=C["card"], fg=C["ink"],
                         font=(FONT, 10, "bold"), anchor="w")
    det_title.pack(fill="x", padx=12, pady=(10, 2))
    tk.Label(sp_right, text="to see every merchant inside it", bg=C["card"],
             fg=C["muted"], font=(FONT, 8), anchor="w").pack(fill="x", padx=12)
    detail = ttk.Treeview(sp_right, columns=("amt",), show="tree headings", height=22)
    detail.heading("#0", text="Merchant")
    detail.heading("amt", text="Avg/mo")
    detail.column("#0", width=160)
    detail.column("amt", width=80, anchor="e")
    detail.pack(fill="both", expand=True, padx=10, pady=10)

    def show_detail(cat):
        for i in detail.get_children():
            detail.delete(i)
        det_title.config(text=cat)
        items = store.merchants_in(cat)
        for mname, v in items.items():
            detail.insert("", "end", text=mname, values=(M(v),))
        if not items:
            detail.insert("", "end", text="(nothing here)", values=("",))

    sp_widgets = {}

    def sp_summary_update():
        base = store.spend_total()
        plan = store.planned_spend()
        fc = store.free_cash(1)
        delta = base - plan
        word = ("trimming " + M(delta) if delta > 0.5 else
                ("spending " + M(-delta) + " more" if delta < -0.5 else "at your baseline"))
        sp_now.config(text=f"Budget {M(plan)}/mo   →   {M(fc)}/mo toward debt",
                      fg=C["good"] if fc > 0 else C["bad"])
        r = project(store)
        when = (month_label(store.settings["start"], r["months"])
                if r["months"] else "never at this budget")
        sp_sub.config(text=f"   {word} from {M(base)}    ·    debt-free {when}")

    def set_budget(cat, newval, base):
        newval = max(0.0, min(float(newval), base * 2))
        store.cuts[cat] = base - newval
        w = sp_widgets.get(cat)
        if w:
            d = base - newval
            if d > 0.5:
                w["delta"].config(text="−" + M(d), fg=C["okink"])
            elif d < -0.5:
                w["delta"].config(text="+" + M(-d), fg=C["bad"])
            else:
                w["delta"].config(text="baseline", fg=C["muted"])
            w["newlab"].config(text=M(newval))
            w["bar"].config(width=max(int(min(newval / (base * 2 or 1), 1.0) * 150), 2),
                            bg=w["col"] if newval <= base else C["bad"])
        t = sp_widgets.get("__totals__")
        if t:
            t["fn"]()
        sp_summary_update()

    def commit():
        save()
        refresh_dash()

    def refresh_spend():
        for w in sp_rows.winfo_children():
            w.destroy()
        sp_widgets.clear()
        by = store.spend_by_category()
        trimmable = [(c, a) for c, a in by.items() if a > 0.5]
        flat = [(c, a) for c, a in by.items() if a <= 0.5]

        intro = tk.Frame(sp_rows, bg=C["card"])
        intro.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(intro, text="Set a monthly budget for each category.", bg=C["card"],
                 fg=C["ink"], font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(intro, text="Slide left to spend less, right to spend more — so if you "
                             "cut eating out and groceries go up, you can show both. "
                             "The − and + buttons step $25.",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8), justify="left",
                 wraplength=620).pack(anchor="w")

        hdr = tk.Frame(sp_rows, bg=C["card"])
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        for txt, wd, an in (("CATEGORY", 20, "w"), ("NOW", 10, "e"),
                            ("BUDGET", 30, "center"), ("NEW", 10, "e"),
                            ("CHANGE", 11, "e"), ("TREND", 13, "center"),
                            ("PAID FROM", 16, "w")):
            tk.Label(hdr, text=txt, width=wd, anchor=an, bg=C["card"], fg=C["muted"],
                     font=(FONT, 8, "bold")).pack(side="left")

        for idx, (cat, base) in enumerate(trimmable):
            col = SERIES[idx % len(SERIES)]
            row = tk.Frame(sp_rows, bg=C["card"])
            row.pack(fill="x", padx=14, pady=2)
            tk.Frame(row, bg=col, width=5, height=30).pack(side="left", padx=(0, 6))
            tk.Button(row, text=cat[:18], width=17, anchor="w", relief="flat",
                      bg=C["card"], fg=C["ink"], font=(FONT, 9), cursor="hand2",
                      activebackground=C["wash"],
                      command=lambda c=cat: show_detail(c)).pack(side="left")
            tk.Label(row, text=M(base), width=10, anchor="e", bg=C["card"],
                     fg=C["muted"], font=(FONT, 9)).pack(side="left")

            def mk_on(c=cat, bs=base):
                def on(v):
                    set_budget(c, float(v), bs)
                return on
            ctl, setv, getv = num_control(row, base - store.cuts.get(cat, 0.0),
                                          0, base * 2, mk_on(cat, base),
                                          fmt=lambda v: f"{v:,.0f}", step=25,
                                          width=8, length=150)
            ctl.pack(side="left", padx=(4, 6))

            newlab = tk.Label(row, width=10, anchor="e", bg=C["card"], fg=C["ink"],
                              font=(FONT, 9, "bold"))
            newlab.pack(side="left")
            delta = tk.Label(row, width=11, anchor="e", bg=C["card"], font=(FONT, 9))
            delta.pack(side="left")
            barwrap = tk.Frame(row, bg=C["line"], height=5, width=150)
            bar = tk.Frame(barwrap, bg=col, height=5, width=2)
            bar.pack(side="left")
            spark = tk.Canvas(row, width=86, height=24, bg=C["card"],
                              highlightthickness=0)
            spark.pack(side="left", padx=(10, 0))
            draw_spark(spark, store.category_series(cat), col)
            owner = store.assigned_categories().get(cat, "")
            av = tk.StringVar(value=owner or "— no account —")
            names = ["— no account —"] + [x.name for x in store.accounts]

            def mk_assign(c=cat, v=av):
                def on(_e=None):
                    push_undo("assign a category")
                    store.assign_category(c, "" if v.get().startswith("—") else v.get())
                    save()
                    refresh_all()
                return on
            cbx = ttk.Combobox(row, textvariable=av, values=names, width=15,
                               state="readonly")
            cbx.pack(side="left", padx=(10, 0))
            cbx.bind("<<ComboboxSelected>>", mk_assign(cat, av))

            sp_widgets[cat] = {"set": setv, "delta": delta, "newlab": newlab,
                               "bar": bar, "col": col, "base": base}
            set_budget(cat, base - store.cuts.get(cat, 0.0), base)

        for cat, avg in flat:
            row = tk.Frame(sp_rows, bg=C["card"])
            row.pack(fill="x", padx=14, pady=2)
            tk.Frame(row, bg=C["line"], width=5, height=24).pack(side="left", padx=(0, 6))
            tk.Button(row, text=cat[:18], width=17, anchor="w", relief="flat",
                      bg=C["card"], fg=C["muted"], font=(FONT, 9), cursor="hand2",
                      command=lambda c=cat: show_detail(c)).pack(side="left")
            tk.Label(row, text=M(avg), width=10, anchor="e", bg=C["card"],
                     fg=C["muted"], font=(FONT, 9)).pack(side="left")
            tk.Label(row, text="net credit — nothing to budget", bg=C["card"],
                     fg=C["muted"], font=(FONT, 8)).pack(side="left", padx=10)

        ttk.Separator(sp_rows).pack(fill="x", padx=14, pady=8)
        base_tot = store.spend_total()
        f = tk.Frame(sp_rows, bg=C["card"])
        f.pack(fill="x", padx=14, pady=(0, 4))
        tk.Frame(f, bg=C["ink"], width=5, height=22).pack(side="left", padx=(0, 6))
        tk.Label(f, text="TOTAL", width=17, anchor="w", bg=C["card"], fg=C["ink"],
                 font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(f, text=M(base_tot), width=10, anchor="e", bg=C["card"], fg=C["muted"],
                 font=(FONT, 9)).pack(side="left")
        tk.Label(f, text="", width=30, bg=C["card"]).pack(side="left")
        tnew = tk.Label(f, width=10, anchor="e", bg=C["card"], fg=C["ink"],
                        font=(FONT, 10, "bold"))
        tnew.pack(side="left")
        tdel = tk.Label(f, width=11, anchor="e", bg=C["card"], font=(FONT, 9, "bold"))
        tdel.pack(side="left")

        def totals():
            plan = store.planned_spend()
            d = base_tot - plan
            tnew.config(text=M(plan))
            if d > 0.5:
                tdel.config(text="−" + M(d), fg=C["okink"])
            elif d < -0.5:
                tdel.config(text="+" + M(-d), fg=C["bad"])
            else:
                tdel.config(text="baseline", fg=C["muted"])
        sp_widgets["__totals__"] = {"fn": totals}
        totals()

        ms = store.baseline_months()
        tk.Label(sp_rows, text=f"Averages use {len(ms)} month(s): {', '.join(ms)}.",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8), justify="left",
                 anchor="w", wraplength=620).pack(fill="x", padx=14, pady=(6, 2))

        # month-by-month trend: an average hides whether you're improving
        series = store.monthly_spend_series()
        if len([v for v in series.values() if v > 0]) >= 2:
            tw = tk.Frame(sp_rows, bg=C["card"])
            tw.pack(fill="x", padx=14, pady=(10, 2))
            tk.Label(tw, text="WHAT YOU ACTUALLY SPENT, MONTH BY MONTH",
                     bg=C["card"], fg=C["muted"],
                     font=(FONT, 8, "bold")).pack(anchor="w")
            tc = tk.Canvas(tw, height=132, bg=C["card"], highlightthickness=0)
            tc.pack(fill="x", pady=4)
            tc.bind("<Configure>",
                    lambda e, sr=series: draw_trend(e.widget, sr))
            tc.after(60, lambda c=tc, sr=series: draw_trend(c, sr))

        bad = store.settings.get("unreadable", [])
        if bad:
            wb = tk.Frame(sp_rows, bg=C["warnbg"], highlightthickness=1,
                          highlightbackground=C["warn"])
            wb.pack(fill="x", padx=14, pady=(8, 2))
            tk.Label(wb, text=f"{len(bad)} STATEMENT FILE(S) COULD NOT BE READ",
                     bg=C["warnbg"], fg=C["warnink"],
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
            for b in bad[:6]:
                tk.Label(wb, text="• " + b, bg=C["warnbg"], fg=C["ink2"],
                         font=(FONT, 8), anchor="w").pack(fill="x", padx=16)
            tk.Label(wb, text=("These are almost certainly printed to PDF from a "
                               "browser, which makes a picture with no text in it. "
                               "Download the statement using the bank's own PDF "
                               "button, or export CSV, then scan again."),
                     bg=C["warnbg"], fg=C["ink2"], font=(FONT, 8), justify="left",
                     wraplength=580, anchor="w").pack(fill="x", padx=10, pady=(4, 10))

        trips = store.trips()
        if trips:
            ex_box = tk.Frame(sp_rows, bg=C["wash"])
            ex_box.pack(fill="x", padx=14, pady=(6, 4))
            tk.Label(ex_box, text="LEFT OUT OF THESE AVERAGES", bg=C["wash"],
                     fg=C["ink2"], font=(FONT, 8, "bold")).pack(
                         anchor="w", padx=10, pady=(8, 4))
            n_mo = max(len(ms), 1)
            for label, (amt, cnt) in sorted(trips.items(), key=lambda x: -x[1][0]):
                r = tk.Frame(ex_box, bg=C["wash"])
                r.pack(fill="x", padx=10, pady=1)
                tk.Label(r, text=label, bg=C["wash"], fg=C["ink"], font=(FONT, 9),
                         anchor="w").pack(side="left")
                tk.Label(r, text=f"{cnt} charges", bg=C["wash"], fg=C["muted"],
                         font=(FONT, 8)).pack(side="left", padx=8)
                tk.Label(r, text=M(amt), bg=C["wash"], fg=C["ink"],
                         font=(FONT, 9, "bold")).pack(side="right")
            tot_ex = sum(v[0] for v in trips.values())
            tk.Label(ex_box, text=(
                f"These {M(tot_ex)} of charges really happened and really left your "
                f"account — they are just not treated as a MONTHLY habit, so they "
                f"don't distort the averages above or the payoff plan.\n\n"
                f"Spread over {n_mo} months they'd add {M(tot_ex/n_mo)}/mo. If you "
                f"take trips regularly, that is not free money: set a savings goal "
                f"for the next one instead of letting it land on a card."),
                bg=C["wash"], fg=C["ink2"], font=(FONT, 8), justify="left",
                wraplength=600, anchor="w").pack(fill="x", padx=10, pady=(6, 10))
        tk.Frame(sp_rows, bg=C["card"], height=10).pack()
        sp_summary_update()

    def draw_spark(cv, series, col):
        """Tiny 6-month shape for one category — direction at a glance."""
        cv.delete("all")
        pts = [v for _, v in sorted(series.items())]
        if len(pts) < 2:
            return
        W, H, pad = 86, 24, 3
        lo, hi = min(pts), max(pts)
        rng = (hi - lo) or 1
        xs = [pad + (W - 2 * pad) * i / (len(pts) - 1) for i in range(len(pts))]
        ys = [H - pad - (H - 2 * pad) * (v - lo) / rng for v in pts]
        cv.create_line(*[c for p in zip(xs, ys) for c in p], fill=col, width=2)
        cv.create_oval(xs[-1] - 2.5, ys[-1] - 2.5, xs[-1] + 2.5, ys[-1] + 2.5,
                       fill=col, outline=C["card"])

    def draw_trend(cv, series):
        cv.delete("all")
        pts = [(m, v) for m, v in sorted(series.items())]
        if len(pts) < 2:
            return
        W = max(cv.winfo_width(), 420)
        H, ml, mr, mt, mb = 132, 62, 14, 12, 26
        iw, ih = W - ml - mr, H - mt - mb
        vals = [v for _, v in pts]
        top = max(vals) * 1.15 or 1
        y = lambda v: mt + ih - (v / top) * ih
        x = lambda i: ml + (iw * i / max(len(pts) - 1, 1))
        avg = sum(vals) / len(vals)
        for g in (0, top / 2, top):
            cv.create_line(ml, y(g), W - mr, y(g), fill=C["line"])
            cv.create_text(ml - 8, y(g), text=f"${g:,.0f}", anchor="e",
                           fill=C["muted"], font=(FONT, 8))
        cv.create_line(ml, y(avg), W - mr, y(avg), fill=C["axis"], dash=(4, 3))
        cv.create_text(W - mr, y(avg) - 8, text=f"average {M0(avg)}", anchor="e",
                       fill=C["muted"], font=(FONT, 8))
        cv.create_line(*[c for i, (_, v) in enumerate(pts) for c in (x(i), y(v))],
                       fill=SERIES[0], width=2, smooth=False)
        for i, (m, v) in enumerate(pts):
            up = v > avg
            cv.create_oval(x(i) - 4, y(v) - 4, x(i) + 4, y(v) + 4,
                           fill=C["bad"] if up else C["good"], outline=C["card"], width=2)
            cv.create_text(x(i), y(v) - 14, text=M0(v), fill=C["ink"],
                           font=(FONT, 8, "bold"))
            cv.create_text(x(i), H - mb + 12, text=m[5:] + "/" + m[2:4],
                           fill=C["ink2"], font=(FONT, 8))

    REFRESH.append(refresh_spend)

    # ═══ BILLS ════════════════════════════════════════════════════════════
    tab_b = ttk.Frame(nb, padding=10)
    nb.add(tab_b, text="  Bills  ")
    b_tree = ttk.Treeview(tab_b, columns=("amt", "day", "src", "on"), show="tree headings")
    b_tree.heading("#0", text="Bill")
    for c, t, wd in (("amt", "Amount", 100), ("day", "Timing", 110),
                     ("src", "Paid from", 150), ("on", "Active", 70)):
        b_tree.heading(c, text=t)
        b_tree.column(c, width=wd, anchor="e" if c == "amt" else "w")
    b_tree.column("#0", width=280)
    b_tree.pack(side="left", fill="both", expand=True)
    b_idx = {}

    def refresh_bills():
        for i in b_tree.get_children():
            b_tree.delete(i)
        b_idx.clear()
        for b in store.bills:
            iid = b_tree.insert("", "end", text=b.name,
                                values=(M(b.amount), b.day, b.paid_from,
                                        "yes" if b.active else "no"))
            b_idx[iid] = b
        b_tree.insert("", "end", text="TOTAL", values=(M(store.bills_total()), "", "", ""))
    REFRESH.append(refresh_bills)

    def bill_edit(b=None):
        win = tk.Toplevel(root)
        win.title("Bill")
        win.geometry("380x250")
        es = {}
        for i, (lab, val) in enumerate([("Name", b.name if b else ""),
                                        ("Amount", f"{b.amount:.2f}" if b else "0.00"),
                                        ("Timing", b.day if b else ""),
                                        ("Paid from", b.paid_from if b else "Debit")]):
            ttk.Label(win, text=lab).grid(row=i, column=0, sticky="w", padx=10, pady=7)
            e = ttk.Entry(win, width=26)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=10)
            es[lab] = e
        act = tk.BooleanVar(value=b.active if b else True)
        ttk.Checkbutton(win, text="Active", variable=act).grid(row=4, column=1, sticky="w", padx=10)

        def ok():
            try:
                amt = float(es["Amount"].get().replace("$", "").replace(",", ""))
            except ValueError:
                messagebox.showerror("Bad amount", "Enter a number.")
                return
            nonlocal b
            if b is None:
                b = Bill("", 0)
                store.bills.append(b)
            b.name = es["Name"].get().strip() or "Bill"
            b.amount = amt
            b.day = es["Timing"].get().strip()
            b.paid_from = es["Paid from"].get().strip()
            b.active = act.get()
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Save", command=ok).grid(row=5, column=1, pady=14, sticky="e", padx=10)

    def bill_del():
        sel = [b_idx[i] for i in b_tree.selection() if i in b_idx]
        if not sel or not messagebox.askyesno("Delete", f"Delete {len(sel)} bill(s)?"):
            return
        ids = {id(x) for x in sel}
        store.bills = [x for x in store.bills if id(x) not in ids]
        save()
        refresh_all()

    bb = ttk.Frame(tab_b)
    bb.pack(side="left", fill="y", padx=8)
    ttk.Button(bb, text="Add bill…", width=15, command=lambda: bill_edit()).pack(pady=3)
    ttk.Button(bb, text="Edit selected…", width=15,
               command=lambda: [bill_edit(b_idx[i]) for i in b_tree.selection()
                                if i in b_idx][:1]).pack(pady=3)
    ttk.Button(bb, text="Delete", width=15, command=bill_del).pack(pady=3)

    # ═══ DEBTS ════════════════════════════════════════════════════════════
    tab_d = ttk.Frame(nb, padding=10)
    nb.add(tab_d, text="  Debts  ")
    d_tree = ttk.Treeview(tab_d,
                          columns=("last4", "bal", "asof", "apr", "min", "promo", "on"),
                          show="tree headings")
    d_tree.heading("#0", text="Account")
    for c, t, wd in (("last4", "Ends", 55), ("bal", "Balance", 100),
                     ("asof", "Balance as of", 130), ("apr", "APR", 70),
                     ("min", "Minimum", 80), ("promo", "0% until", 95),
                     ("on", "In plan", 65)):
        d_tree.heading(c, text=t)
        d_tree.column(c, width=wd, anchor="e" if c in ("bal", "apr", "min") else "w")
    d_tree.column("#0", width=220)
    d_tree.pack(side="left", fill="both", expand=True)
    d_idx = {}

    def refresh_debts():
        for i in d_tree.get_children():
            d_tree.delete(i)
        d_idx.clear()
        for d in store.debts:
            src = {"statement": "from statement", "manual": "you typed it"}.get(
                d.balance_source, "")
            asof = f"{d.balance_asof}  {src}".strip() if d.balance_asof else "—"
            iid = d_tree.insert("", "end", text=d.name,
                                values=(d.last4 or "—", M(d.balance), asof,
                                        f"{d.apr*100:.2f}%", M(d.minimum),
                                        d.promo_until,
                                        "yes" if d.include else "no"))
            d_idx[iid] = d
    REFRESH.append(refresh_debts)

    def debt_edit(d=None):
        win = tk.Toplevel(root)
        win.title("Debt")
        win.geometry("440x360")
        es = {}
        rows = [("Name", d.name if d else ""),
                ("Last 4 of account #", d.last4 if d else ""),
                ("Balance", f"{d.balance:.2f}" if d else "0.00"),
                ("APR % (e.g. 28.49)", f"{d.apr*100:.2f}" if d else "0.00"),
                ("Minimum payment", f"{d.minimum:.2f}" if d else "25.00"),
                ("0% promo ends (YYYY-MM-DD, blank if none)", d.promo_until if d else ""),
                ("Rate after promo % ", f"{d.promo_apr_after*100:.2f}" if d else "0.00")]
        for i, (lab, val) in enumerate(rows):
            ttk.Label(win, text=lab, wraplength=210).grid(row=i, column=0, sticky="w",
                                                          padx=10, pady=5)
            e = ttk.Entry(win, width=20)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=10)
            es[lab] = e
        inc = tk.BooleanVar(value=d.include if d else True)
        ttk.Checkbutton(win, text="Include in the plan", variable=inc).grid(
            row=len(rows), column=1, sticky="w", padx=10)

        def ok():
            nonlocal d
            try:
                bal = float(es["Balance"].get().replace("$", "").replace(",", ""))
                apr = float(es["APR % (e.g. 28.49)"].get().replace("%", "")) / 100
                mn = float(es["Minimum payment"].get().replace("$", "").replace(",", ""))
                pa = float(es["Rate after promo % "].get().replace("%", "") or 0) / 100
            except ValueError:
                messagebox.showerror("Bad number", "Check the numeric fields.")
                return
            if d is None:
                d = Debt("", 0, 0, 0)
                store.debts.append(d)
            d.name = es["Name"].get().strip() or "Card"
            d.last4 = re.sub(r"\D", "", es["Last 4 of account #"].get())[-4:]
            if abs(bal - d.balance) > 0.005 or not d.balance_asof:
                # you typed it, so it is true as of today and outranks any
                # statement older than today
                d.balance_asof = core.date.today().isoformat()
                d.balance_source = "manual"
            d.balance, d.apr, d.minimum = bal, apr, mn
            d.promo_until = es["0% promo ends (YYYY-MM-DD, blank if none)"].get().strip()
            d.promo_apr_after = pa
            d.include = inc.get()
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Save", command=ok).grid(row=len(rows) + 1, column=1,
                                                      pady=14, sticky="e", padx=10)

    def debt_del():
        sel = [d_idx[i] for i in d_tree.selection() if i in d_idx]
        if not sel or not messagebox.askyesno("Delete", f"Delete {len(sel)} account(s)?"):
            return
        ids = {id(x) for x in sel}
        store.debts = [x for x in store.debts if id(x) not in ids]
        save()
        refresh_all()

    db = ttk.Frame(tab_d)
    db.pack(side="left", fill="y", padx=8)
    ttk.Button(db, text="Add account…", width=16, command=lambda: debt_edit()).pack(pady=3)
    ttk.Button(db, text="Edit selected…", width=16,
               command=lambda: [debt_edit(d_idx[i]) for i in d_tree.selection()
                                if i in d_idx][:1]).pack(pady=3)
    ttk.Button(db, text="Delete", width=16, command=debt_del).pack(pady=3)
    tk.Label(db, text=("\nIf you hold TWO cards from\nthe same issuer, fill in the\n"
                       "last 4 on each. The app\nmatches statements by that\n"
                       "number and will refuse to\nguess between them."),
             bg=C["bg"], fg=C["muted"], font=(FONT, 8), justify="left").pack(pady=6)

    inc_f = ttk.LabelFrame(tab_d, text="Income & rent", padding=10)
    inc_f.pack(side="left", fill="y", padx=10)
    sv = {}
    for i, (lab, key) in enumerate([("Take-home per paycheck", "paycheck"),
                                    ("Paychecks per month", "checks_per_month"),
                                    ("Rent", "rent"),
                                    ("Other income /mo", "extra_income")]):
        ttk.Label(inc_f, text=lab).grid(row=i, column=0, sticky="w", pady=4)
        e = ttk.Entry(inc_f, width=12)
        e.insert(0, str(store.settings.get(key, 0)))
        e.grid(row=i, column=1, padx=6)
        sv[key] = e

    def apply_income():
        try:
            for k, e in sv.items():
                store.settings[k] = float(e.get().replace("$", "").replace(",", ""))
        except ValueError:
            messagebox.showerror("Bad number", "Check the income fields.")
            return
        save()
        refresh_all()
    ttk.Button(inc_f, text="Apply", command=apply_income).grid(row=9, column=1,
                                                               sticky="e", pady=8)

    # ═══ SPENDING ACCOUNTS ════════════════════════════════════════════════
    tab_ac = ttk.Frame(nb, padding=0)
    nb.add(tab_ac, text="  Accounts  ")

    ac_c = tk.Canvas(tab_ac, bg=C["bg"], highlightthickness=0)
    ac_sb = ttk.Scrollbar(tab_ac, orient="vertical", command=ac_c.yview)
    ac_body = tk.Frame(ac_c, bg=C["bg"])
    ac_body.bind("<Configure>", lambda e: ac_c.configure(scrollregion=ac_c.bbox("all")))
    ac_w = ac_c.create_window((0, 0), window=ac_body, anchor="nw")
    ac_c.bind("<Configure>", lambda e: ac_c.itemconfig(ac_w, width=e.width))
    ac_c.configure(yscrollcommand=ac_sb.set)
    ac_c.pack(side="left", fill="both", expand=True)
    ac_sb.pack(side="right", fill="y")

    def account_add():
        n = simpledialog.askstring(
            "New spending account",
            "What do you call it?\n(e.g. Daily Spending, Household, Bills)")
        if not n or not n.strip():
            return
        push_undo("add an account")
        store.accounts.append(core.Account(n.strip(), [], [], 50.0, "",
                                           len(store.accounts)))
        save()
        refresh_all()

    def account_rename(a):
        n = simpledialog.askstring("Rename", "Name:", initialvalue=a.name)
        if n and n.strip():
            push_undo("rename an account")
            a.name = n.strip()
            save()
            refresh_all()

    def account_del(a):
        if not messagebox.askyesno(
                "Delete",
                f"Delete '{a.name}'?\n\nIts categories and bills go back to "
                "unassigned. Nothing about your spending changes."):
            return
        push_undo("delete an account")
        store.accounts = [x for x in store.accounts if x is not a]
        save()
        refresh_all()

    def pick_items(a):
        """Tick which categories and bills this account pays for."""
        win = tk.Toplevel(root)
        win.title(f"What does {a.name} pay for?")
        win.geometry("560x620")
        win.configure(bg=C["bg"])
        tk.Label(win, text=f"What comes out of {a.name}?", bg=C["bg"], fg=C["ink"],
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="Anything you tick here moves to this account. A "
                           "category or bill can only live in one account, so "
                           "ticking it removes it from any other.",
                 bg=C["bg"], fg=C["ink2"], font=(FONT, 9), wraplength=520,
                 justify="left").pack(anchor="w", padx=16)

        cv = tk.Canvas(win, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(win, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=C["bg"])
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="top", fill="both", expand=True, padx=10, pady=8)
        sb.pack(side="right", fill="y")

        owner_c = store.assigned_categories()
        owner_b = store.assigned_bills()
        cvars, bvars = {}, {}

        tk.Label(inner, text="SPENDING CATEGORIES", bg=C["bg"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=8, pady=(6, 3))
        for cat, amt in store.spend_by_category().items():
            if amt <= 0:
                continue
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", padx=8)
            v = tk.BooleanVar(value=cat in a.categories)
            cvars[cat] = v
            ttk.Checkbutton(row, variable=v).pack(side="left")
            tk.Label(row, text=cat, bg=C["bg"], fg=C["ink"], font=(FONT, 9),
                     width=26, anchor="w").pack(side="left")
            tk.Label(row, text=M(store.category_budget(cat)), bg=C["bg"],
                     fg=C["ink2"], font=(FONT, 9), width=11,
                     anchor="e").pack(side="left")
            other = owner_c.get(cat)
            if other and other != a.name:
                tk.Label(row, text=f"now in {other}", bg=C["bg"], fg=C["warnink"],
                         font=(FONT, 8)).pack(side="left", padx=8)

        tk.Label(inner, text="BILLS", bg=C["bg"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=8, pady=(12, 3))
        for b in store.bills:
            if not b.active:
                continue
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", padx=8)
            v = tk.BooleanVar(value=b.name in a.bills)
            bvars[b.name] = v
            ttk.Checkbutton(row, variable=v).pack(side="left")
            tk.Label(row, text=b.name[:30], bg=C["bg"], fg=C["ink"], font=(FONT, 9),
                     width=26, anchor="w").pack(side="left")
            tk.Label(row, text=M(b.amount), bg=C["bg"], fg=C["ink2"], font=(FONT, 9),
                     width=11, anchor="e").pack(side="left")
            other = owner_b.get(b.name)
            if other and other != a.name:
                tk.Label(row, text=f"now in {other}", bg=C["bg"], fg=C["warnink"],
                         font=(FONT, 8)).pack(side="left", padx=8)

        def ok():
            push_undo("change what an account pays for")
            for cat, v in cvars.items():
                if v.get():
                    store.assign_category(cat, a.name)
                elif cat in a.categories:
                    a.categories.remove(cat)
            for nm, v in bvars.items():
                if v.get():
                    store.assign_bill(nm, a.name)
                elif nm in a.bills:
                    a.bills.remove(nm)
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Save", command=ok).pack(pady=10)

    def refresh_accounts():
        for w in ac_body.winfo_children():
            w.destroy()
        plan = store.paycheck_plan()
        pay = store.settings.get("paycheck", 0.0)

        hero = tk.Frame(ac_body, bg=C["hero"])
        hero.pack(fill="x")
        hi = tk.Frame(hero, bg=C["hero"])
        hi.pack(fill="x", padx=20, pady=14)
        tk.Label(hi, text="MOVE THIS OUT OF EACH PAYCHECK", bg=C["hero"],
                 fg=C["herosub"], font=(FONT, 8, "bold")).pack(anchor="w")
        row = tk.Frame(hi, bg=C["hero"])
        row.pack(anchor="w")
        tk.Label(row, text=M0(plan["first"]), bg=C["hero"], fg=C["heroink"],
                 font=(FONT, 30, "bold")).pack(side="left")
        tk.Label(row, text="   1st check      ", bg=C["hero"], fg=C["herosub"],
                 font=(FONT, 10)).pack(side="left", pady=(12, 0))
        tk.Label(row, text=M0(plan["second"]), bg=C["hero"], fg=C["heroink"],
                 font=(FONT, 30, "bold")).pack(side="left")
        tk.Label(row, text="   2nd check", bg=C["hero"], fg=C["herosub"],
                 font=(FONT, 10)).pack(side="left", pady=(12, 0))
        left1 = pay - plan["first"]
        left2 = pay - plan["second"]
        tk.Label(hi, text=(f"{M(plan['total'])} a month — "
                           f"{M(plan['accounts_first'] + plan['accounts_second'])} "
                           f"to {len(plan['rows'])} account(s) and "
                           f"{M(plan['cards_total'])} to cards.   "
                           f"Leaves {M(left1)} of the 1st check and {M(left2)} of "
                           f"the 2nd."),
                 bg=C["hero"], fg=C["herosub"], font=(FONT, 10)).pack(anchor="w",
                                                                     pady=(4, 0))
        if pay > 0 and (plan["first"] > pay or plan["second"] > pay):
            tk.Label(hi, text="One of these is bigger than a single paycheck — "
                             "shift the split.", bg=C["hero"], fg=C["warnink"],
                     font=(FONT, 9, "bold")).pack(anchor="w", pady=(4, 0))

        head = tk.Frame(ac_body, bg=C["bg"])
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(head, text="YOUR ACCOUNTS", bg=C["bg"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(side="left")
        ttk.Button(head, text="New account…", command=account_add).pack(side="right")

        if not store.accounts:
            tk.Label(ac_body, text=(
                "No accounts yet.\n\nThe idea: give each debit card or envelope a "
                "name, tick which categories and bills come out of it, and the app "
                "works out how much to move across on each payday.\n\n"
                "A common setup is three — Daily Spending, Household, and Bills."),
                bg=C["bg"], fg=C["ink2"], font=(FONT, 10), justify="left",
                wraplength=680).pack(anchor="w", padx=18, pady=10)

        for i, a in enumerate(store.accounts):
            col = SERIES[a.color % len(SERIES)] if a.color else SERIES[i % len(SERIES)]
            b = store.account_breakdown(a)
            card = tk.Frame(ac_body, bg=C["card"], highlightthickness=1,
                            highlightbackground=C["line"])
            card.pack(fill="x", padx=14, pady=6)
            tk.Frame(card, bg=col, height=4).pack(fill="x")

            top = tk.Frame(card, bg=C["card"])
            top.pack(fill="x", padx=14, pady=(10, 2))
            tk.Label(top, text=a.name, bg=C["card"], fg=C["ink"],
                     font=(FONT, 13, "bold")).pack(side="left")
            tk.Label(top, text=M(b["total"]) + " / month", bg=C["card"], fg=C["ink"],
                     font=(FONT, 12, "bold")).pack(side="right")
            for lab, fn in (("✕", lambda x=a: account_del(x)),
                            ("rename", lambda x=a: account_rename(x)),
                            ("what it pays for…", lambda x=a: pick_items(x))):
                tk.Button(top, text=lab, relief="flat", bg=C["card"],
                          fg=C["muted"] if lab == "✕" else SERIES[0],
                          font=(FONT, 8, "bold"), cursor="hand2", padx=6,
                          command=fn).pack(side="right", padx=4)

            # the two paycheck amounts, big and unmissable
            pcw = tk.Frame(card, bg=C["raised"])
            pcw.pack(fill="x", padx=14, pady=8)
            for idx, (lab, amt, pct) in enumerate(
                    (("FROM THE 1st CHECK", b["first"], b["pct_first"]),
                     ("FROM THE 2nd CHECK", b["second"], b["pct_second"]))):
                cell = tk.Frame(pcw, bg=C["raised"])
                cell.pack(side="left", expand=True, fill="x", padx=14, pady=10)
                tk.Label(cell, text=lab, bg=C["raised"], fg=C["muted"],
                         font=(FONT, 8, "bold")).pack(anchor="w")
                tk.Label(cell, text=M(amt), bg=C["raised"], fg=col,
                         font=(FONT, 20, "bold")).pack(anchor="w")
                pin = b["pinned_first"] if idx == 0 else b["pinned_second"]
                sub = (f"{M(pin)} pinned + {pct:.0f}% of the {M(b['flexible'])} "
                       f"that splits" if pin > 0
                       else f"{pct:.0f}% of the {M(b['flexible'])} that splits")
                tk.Label(cell, text=sub, bg=C["raised"], fg=C["ink2"],
                         font=(FONT, 8)).pack(anchor="w")

            # split control
            srow = tk.Frame(card, bg=C["card"])
            srow.pack(fill="x", padx=14, pady=(0, 6))
            tk.Label(srow, text="Split", bg=C["card"], fg=C["ink"], font=(FONT, 9),
                     width=6, anchor="w").pack(side="left")

            def mk_split(acc=a):
                def on(v):
                    acc.split = float(v)
                    bb = store.account_breakdown(acc)
                    lbl = ACW.get(id(acc))
                    if lbl:
                        lbl["first"].config(text=M(bb["first"]))
                        lbl["second"].config(text=M(bb["second"]))
                        lbl["p1"].config(text=f"{bb['pct_first']:.0f}% of the monthly total")
                        lbl["p2"].config(text=f"{bb['pct_second']:.0f}% of the monthly total")
                return on
            ctl, _sv, _gv = num_control(srow, a.split, 0, 100, mk_split(a),
                                        fmt=lambda v: f"{v:.0f}", step=5,
                                        width=6, length=220)
            ctl.pack(side="left")
            tk.Label(srow, text="% from the 1st check", bg=C["card"], fg=C["muted"],
                     font=(FONT, 8)).pack(side="left", padx=8)
            for lab, val in (("50 / 50", 50), ("60 / 40", 60), ("70 / 30", 70)):
                tk.Button(srow, text=lab, relief="flat", bg=C["wash"], fg=C["ink"],
                          font=(FONT, 8), cursor="hand2", padx=6,
                          command=(lambda acc=a, v=val: (
                              setattr(acc, "split", float(v)), save(), refresh_all()))
                          ).pack(side="right", padx=3)

            cells = pcw.winfo_children()
            try:
                ACW[id(a)] = {
                    "first": cells[0].winfo_children()[1],
                    "p1": cells[0].winfo_children()[2],
                    "second": cells[1].winfo_children()[1],
                    "p2": cells[1].winfo_children()[2]}
            except Exception:
                pass

            # what's inside
            body = tk.Frame(card, bg=C["card"])
            body.pack(fill="x", padx=14, pady=(0, 12))
            if not a.categories and not a.bills:
                tk.Label(body, text="Nothing assigned yet — use “what it pays for…”.",
                         bg=C["card"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w")
            for cat in a.categories:
                r = tk.Frame(body, bg=C["card"])
                r.pack(fill="x", pady=1)
                tk.Frame(r, bg=col, width=3, height=14).pack(side="left", padx=(0, 8))
                tk.Label(r, text=cat, bg=C["card"], fg=C["ink"], font=(FONT, 9),
                         width=30, anchor="w").pack(side="left")
                tk.Label(r, text=M(store.category_budget(cat)), bg=C["card"],
                         fg=C["ink2"], font=(FONT, 9), width=11,
                         anchor="e").pack(side="left")
                tk.Label(r, text="spending", bg=C["card"], fg=C["muted"],
                         font=(FONT, 8)).pack(side="left", padx=8)
            for nm in a.bills:
                bo = store.bill_obj(nm)
                r = tk.Frame(body, bg=C["card"])
                r.pack(fill="x", pady=1)
                pinned = bo and bo.check in (1, 2)
                tk.Frame(r, bg=col if pinned else C["axis"], width=3,
                         height=16).pack(side="left", padx=(0, 8))
                tk.Label(r, text=nm[:32], bg=C["card"], fg=C["ink"], font=(FONT, 9),
                         width=28, anchor="w").pack(side="left")
                tk.Label(r, text=M(store.bill_amount(nm)), bg=C["card"], fg=C["ink2"],
                         font=(FONT, 9), width=11, anchor="e").pack(side="left")
                cur = {0: "split", 1: "1st check", 2: "2nd check"}.get(
                    bo.check if bo else 0, "split")
                bv = tk.StringVar(value=cur)

                def mk_bill_check(name=nm, var=bv):
                    def on(_e=None):
                        push_undo("change when a bill is paid")
                        b2 = store.bill_obj(name)
                        if b2:
                            b2.check = {"split": 0, "1st check": 1,
                                        "2nd check": 2}[var.get()]
                        save()
                        refresh_all()
                    return on
                cb = ttk.Combobox(r, textvariable=bv, width=10, state="readonly",
                                  values=["split", "1st check", "2nd check"])
                cb.pack(side="left", padx=10)
                cb.bind("<<ComboboxSelected>>", mk_bill_check(nm, bv))
                if pinned:
                    tk.Label(r, text="paid in full from that check", bg=C["card"],
                             fg=C["okink"], font=(FONT, 8)).pack(side="left")

        # ---- card payments, and which check they come from ----
        cards = plan["cards"]
        if cards:
            cw = tk.Frame(ac_body, bg=C["card"], highlightthickness=1,
                          highlightbackground=C["line"])
            cw.pack(fill="x", padx=14, pady=6)
            tk.Frame(cw, bg=SERIES[7], height=4).pack(fill="x")
            ch = tk.Frame(cw, bg=C["card"])
            ch.pack(fill="x", padx=14, pady=(10, 2))
            tk.Label(ch, text="CARD PAYMENTS", bg=C["card"], fg=C["muted"],
                     font=(FONT, 8, "bold")).pack(side="left")
            tk.Label(ch, text=M(plan["cards_total"]) + " / month",
                     bg=C["card"], fg=C["ink"],
                     font=(FONT, 12, "bold")).pack(side="right")
            tk.Label(cw, text="Amounts come straight from the Plan tab for this "
                             "month. Choose which paycheck each one leaves from.",
                     bg=C["card"], fg=C["ink2"], font=(FONT, 9)).pack(
                         anchor="w", padx=14)
            for i2, c in enumerate(cards):
                r = tk.Frame(cw, bg=C["card"])
                r.pack(fill="x", padx=14, pady=2)
                tk.Frame(r, bg=SERIES[i2 % len(SERIES)], width=3,
                         height=16).pack(side="left", padx=(0, 8))
                tk.Label(r, text=c["name"][:26], bg=C["card"], fg=C["ink"],
                         font=(FONT, 9), width=24, anchor="w").pack(side="left")
                tk.Label(r, text=M(c["amount"]), bg=C["card"], fg=C["ink"],
                         font=(FONT, 9, "bold"), width=11,
                         anchor="e").pack(side="left")
                tk.Label(r, text=f"{c['apr']*100:.2f}%", bg=C["card"], fg=C["muted"],
                         font=(FONT, 8), width=8, anchor="e").pack(side="left")
                cv2 = tk.StringVar(value={0: "split", 1: "1st check",
                                          2: "2nd check"}[c["check"]])

                def mk_card_check(nm=c["name"], var=cv2):
                    def on(_e=None):
                        push_undo("change when a card is paid")
                        for d in store.debts:
                            if d.name == nm:
                                d.check = {"split": 0, "1st check": 1,
                                           "2nd check": 2}[var.get()]
                        save()
                        refresh_all()
                    return on
                cb2 = ttk.Combobox(r, textvariable=cv2, width=10, state="readonly",
                                   values=["split", "1st check", "2nd check"])
                cb2.pack(side="left", padx=10)
                cb2.bind("<<ComboboxSelected>>", mk_card_check(c["name"], cv2))
            sr = tk.Frame(cw, bg=C["raised"])
            sr.pack(fill="x", padx=14, pady=10)
            for lab, amt in (("1st check", plan["cards_first"]),
                             ("2nd check", plan["cards_second"])):
                cell = tk.Frame(sr, bg=C["raised"])
                cell.pack(side="left", expand=True, fill="x", padx=14, pady=8)
                tk.Label(cell, text=lab.upper(), bg=C["raised"], fg=C["muted"],
                         font=(FONT, 8, "bold")).pack(anchor="w")
                tk.Label(cell, text=M(amt), bg=C["raised"], fg=SERIES[7],
                         font=(FONT, 17, "bold")).pack(anchor="w")

        # anything not yet in an account
        uc = store.unassigned_categories()
        ub = store.unassigned_bills()
        if uc or ub:
            box = tk.Frame(ac_body, bg=C["warnbg"], highlightthickness=1,
                           highlightbackground=C["warn"])
            box.pack(fill="x", padx=14, pady=8)
            tot = sum(uc.values()) + sum(ub.values())
            tk.Label(box, text=f"NOT IN ANY ACCOUNT — {M(tot)} A MONTH",
                     bg=C["warnbg"], fg=C["warnink"],
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=12, pady=(9, 4))
            for nm, v in list(uc.items()) + list(ub.items()):
                r = tk.Frame(box, bg=C["warnbg"])
                r.pack(fill="x", padx=16)
                tk.Label(r, text="• " + nm[:34], bg=C["warnbg"], fg=C["ink"],
                         font=(FONT, 9), width=34, anchor="w").pack(side="left")
                tk.Label(r, text=M(v), bg=C["warnbg"], fg=C["ink2"],
                         font=(FONT, 9)).pack(side="left")
            tk.Label(box, text="These still come out of your account — they just "
                              "aren't budgeted to a card yet.",
                     bg=C["warnbg"], fg=C["ink2"], font=(FONT, 8)).pack(
                         anchor="w", padx=12, pady=(4, 10))

        # the every-two-weeks truth
        note = tk.Frame(ac_body, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["line"])
        note.pack(fill="x", padx=14, pady=(8, 22))
        yearly = plan["total"] * 12
        per_check = yearly / 26 if yearly else 0
        tk.Label(note, text="ONE THING ABOUT BEING PAID EVERY TWO WEEKS",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8, "bold")).pack(
                     anchor="w", padx=14, pady=(12, 4))
        tk.Label(note, text=(
            f"You get 26 paychecks a year, not 24. Funding {M(plan['total'])} a "
            f"month out of two checks means putting away {M(plan['total']/2)} each "
            f"time — but across 26 checks that comes to "
            f"{M(plan['total']/2*26)} against {M(yearly)} of actual cost.\n\n"
            f"So twice a year you get a whole spare paycheck. Either fund "
            f"{M(per_check)} per check instead and stay exactly level, or keep "
            f"funding half and treat those two extra checks as debt payments. "
            f"The second is usually the better move while the cards are alive."),
            bg=C["card"], fg=C["ink2"], font=(FONT, 9), justify="left",
            wraplength=820).pack(anchor="w", padx=14, pady=(0, 14))

    ACW = {}
    REFRESH.append(refresh_accounts)

    # ═══ SAVINGS ══════════════════════════════════════════════════════════
    tab_sv = ttk.Frame(nb, padding=0)
    nb.add(tab_sv, text="  Savings  ")

    sv_c = tk.Canvas(tab_sv, bg=C["bg"], highlightthickness=0)
    sv_sb = ttk.Scrollbar(tab_sv, orient="vertical", command=sv_c.yview)
    sv_body = tk.Frame(sv_c, bg=C["bg"])
    sv_body.bind("<Configure>", lambda e: sv_c.configure(scrollregion=sv_c.bbox("all")))
    sv_w = sv_c.create_window((0, 0), window=sv_body, anchor="nw")
    sv_c.bind("<Configure>", lambda e: sv_c.itemconfig(sv_w, width=e.width))
    sv_c.configure(yscrollcommand=sv_sb.set)
    sv_c.pack(side="left", fill="both", expand=True)
    sv_sb.pack(side="right", fill="y")

    def goal_edit(g=None):
        win = tk.Toplevel(root)
        win.title("Savings goal")
        win.configure(bg=C["bg"])
        rows = [("What for", g.name if g else ""),
                ("Target amount", f"{g.target:.2f}" if g else "1000.00"),
                ("Saved so far", f"{g.saved:.2f}" if g else "0.00"),
                ("Putting in each month", f"{g.monthly:.2f}" if g else "0.00"),
                ("Interest rate % (APY)", f"{g.apy*100:.2f}" if g else "0.00"),
                ("Note", g.note if g else "")]
        es = {}
        for i, (lab, val) in enumerate(rows):
            tk.Label(win, text=lab, bg=C["bg"], fg=C["ink"], font=(FONT, 9)).grid(
                row=i, column=0, sticky="w", padx=12, pady=6)
            e = ttk.Entry(win, width=24)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=12)
            es[lab] = e

        def ok():
            nonlocal g
            try:
                tgt = float(es["Target amount"].get().replace("$", "").replace(",", ""))
                sav = float(es["Saved so far"].get().replace("$", "").replace(",", ""))
                mo = float(es["Putting in each month"].get().replace("$", "").replace(",", ""))
                apy = float(es["Interest rate % (APY)"].get().replace("%", "") or 0) / 100
            except ValueError:
                messagebox.showerror("Not a number", "Check the numeric fields.")
                return
            if g is None:
                g = core.Goal("", 0)
                store.goals.append(g)
            g.name = es["What for"].get().strip() or "Savings goal"
            g.target, g.saved, g.monthly, g.apy = tgt, sav, mo, apy
            g.note = es["Note"].get().strip()
            win.destroy()
            save()
            refresh_all()
        ttk.Button(win, text="Save", command=ok).grid(row=len(rows), column=1,
                                                      sticky="e", padx=12, pady=12)

    def goal_del(g):
        if messagebox.askyesno("Delete", f"Delete the goal '{g.name}'?"):
            store.goals = [x for x in store.goals if x is not g]
            save()
            refresh_all()

    def refresh_savings():
        for w in sv_body.winfo_children():
            w.destroy()
        fc = store.free_cash(1)
        committed = store.goals_monthly()

        hero = tk.Frame(sv_body, bg=SERIES[2])
        hero.pack(fill="x")
        hi = tk.Frame(hero, bg=SERIES[2])
        hi.pack(fill="x", padx=20, pady=14)
        tot_saved = sum(g.saved for g in store.goals)
        tk.Label(hi, text="TOTAL SAVED", bg=SERIES[2], fg=C["herosub"],
                 font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(hi, text=M0(tot_saved), bg=SERIES[2], fg="#ffffff",
                 font=(FONT, 30, "bold")).pack(anchor="w")
        tk.Label(hi, text=f"across {len(store.goals)} goal(s)   ·   "
                          f"putting in {M(committed)} a month",
                 bg=SERIES[2], fg=C["herosub"], font=(FONT, 10)).pack(anchor="w")

        # reality check against actual free cash
        warn = tk.Frame(sv_body, bg=C["bg"])
        warn.pack(fill="x", padx=14, pady=(12, 4))
        if committed > max(fc, 0):
            b = tk.Frame(warn, bg=C["badbg"], highlightthickness=1,
                         highlightbackground=C["bad"])
            b.pack(fill="x")
            tk.Frame(b, bg=C["bad"], width=5).pack(side="left", fill="y")
            inn = tk.Frame(b, bg=C["badbg"])
            inn.pack(side="left", fill="x", expand=True, padx=12, pady=9)
            tk.Label(inn, text=f"You're committing {M(committed)} a month to savings "
                              f"but only {M(fc)} is actually free",
                     bg=C["badbg"], fg=C["bad"], font=(FONT, 10, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(inn, text="Saving on a credit card is a loss: your cards cost far "
                               "more in interest than any savings account pays. Clear "
                               "the expensive balances first, keep only a small starter "
                               "cushion, then save in earnest.",
                     bg=C["badbg"], fg=C["ink2"], font=(FONT, 9), wraplength=820,
                     justify="left", anchor="w").pack(fill="x")
        else:
            b = tk.Frame(warn, bg=C["goodbg"], highlightthickness=1,
                         highlightbackground=C["good"])
            b.pack(fill="x")
            tk.Label(b, text=f"{M(committed)} a month to savings fits inside your "
                             f"{M(fc)} of free cash.",
                     bg=C["goodbg"], fg=C["okink"], font=(FONT, 10)).pack(
                         anchor="w", padx=14, pady=9)

        hd = tk.Frame(sv_body, bg=C["bg"])
        hd.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(hd, text="GOALS", bg=C["bg"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(side="left")
        ttk.Button(hd, text="Add a goal…", command=lambda: goal_edit()).pack(side="right")

        if not store.goals:
            tk.Label(sv_body, text="No goals yet. A starter emergency fund of about one "
                                   "month of bills is the usual first one.",
                     bg=C["bg"], fg=C["muted"], font=(FONT, 9)).pack(
                         anchor="w", padx=16, pady=8)

        for i, g in enumerate(store.goals):
            col = SERIES[i % len(SERIES)]
            card = tk.Frame(sv_body, bg=C["card"], highlightthickness=1,
                            highlightbackground=C["line"])
            card.pack(fill="x", padx=14, pady=5)
            tk.Frame(card, bg=col, height=4).pack(fill="x")
            top = tk.Frame(card, bg=C["card"])
            top.pack(fill="x", padx=14, pady=(10, 2))
            tk.Label(top, text=g.name, bg=C["card"], fg=C["ink"],
                     font=(FONT, 12, "bold")).pack(side="left")
            tk.Button(top, text="✕", relief="flat", bg=C["card"], fg=C["muted"],
                      cursor="hand2", font=(FONT, 8),
                      command=lambda gg=g: goal_del(gg)).pack(side="right")
            tk.Button(top, text="edit", relief="flat", bg=C["wash"], fg=SERIES[0],
                      cursor="hand2", font=(FONT, 8, "bold"), padx=8,
                      command=lambda gg=g: goal_edit(gg)).pack(side="right", padx=6)
            tk.Label(top, text=f"{M(g.saved)} of {M(g.target)}", bg=C["card"],
                     fg=C["ink2"], font=(FONT, 10)).pack(side="right", padx=14)

            pct = min(g.saved / g.target, 1.0) if g.target > 0 else 0.0
            barw = tk.Frame(card, bg=C["line"], height=12)
            barw.pack(fill="x", padx=14, pady=4)
            tk.Frame(barw, bg=col, height=12,
                     width=max(int(pct * 800), 2)).pack(side="left")

            n = core.months_to_goal(g)
            info = tk.Frame(card, bg=C["card"])
            info.pack(fill="x", padx=14, pady=(2, 12))
            if g.saved >= g.target:
                txt, fg = "Funded.", C["okink"]
            elif n is None:
                txt, fg = ("Not putting anything in — it'll never get there. "
                           "Click edit and set a monthly amount."), C["bad"]
            else:
                txt = (f"{pct*100:.0f}% there · {M(g.monthly)}/mo · "
                       f"funded in {n} month(s), around "
                       f"{month_label(store.settings['start'], n)}")
                fg = C["ink2"]
            tk.Label(info, text=txt, bg=C["card"], fg=fg, font=(FONT, 9),
                     wraplength=800, justify="left", anchor="w").pack(fill="x")
            if g.note:
                tk.Label(info, text=g.note, bg=C["card"], fg=C["muted"],
                         font=(FONT, 8), wraplength=800, justify="left",
                         anchor="w").pack(fill="x")
            if g.apy:
                tk.Label(info, text=f"earning {g.apy*100:.2f}% APY", bg=C["card"],
                         fg=C["muted"], font=(FONT, 8), anchor="w").pack(fill="x")

        tk.Label(sv_body, text="Order that usually works: a small starter cushion "
                               "(~$1,000) → clear the high-rate cards → 3–6 months of "
                               "expenses → the house down payment. Paying off a 28% card "
                               "is a guaranteed 28% return; no savings account competes "
                               "with that.",
                 bg=C["bg"], fg=C["muted"], font=(FONT, 9), wraplength=880,
                 justify="left").pack(anchor="w", padx=16, pady=(12, 24))

    REFRESH.append(refresh_savings)

    # ═══ HOME ═════════════════════════════════════════════════════════════
    tab_hm = ttk.Frame(nb, padding=0)
    nb.add(tab_hm, text="  Home & PMI  ")

    hm_c = tk.Canvas(tab_hm, bg=C["bg"], highlightthickness=0)
    hm_sb = ttk.Scrollbar(tab_hm, orient="vertical", command=hm_c.yview)
    hm_body = tk.Frame(hm_c, bg=C["bg"])
    hm_body.bind("<Configure>", lambda e: hm_c.configure(scrollregion=hm_c.bbox("all")))
    hm_w = hm_c.create_window((0, 0), window=hm_body, anchor="nw")
    hm_c.bind("<Configure>", lambda e: hm_c.itemconfig(hm_w, width=e.width))
    hm_c.configure(yscrollcommand=hm_sb.set)
    hm_c.pack(side="left", fill="both", expand=True)
    hm_sb.pack(side="right", fill="y")

    HM = {}          # live widget handles

    HOME_BOUNDS = {"price": (0, 3_000_000), "down": (0, 2_000_000),
                   "apr": (0.0, 0.30), "years": (1, 40),
                   "tax_rate": (0.0, 0.06), "insurance": (0, 50_000),
                   "pmi_rate": (0.0, 0.03), "hoa": (0, 5_000),
                   "front": (0.05, 0.60), "back": (0.05, 0.70)}

    def hcfg():
        """Home inputs, clamped — a hand-edited or stale data file can't
        produce a nonsense mortgage."""
        h = store.settings.setdefault("home", {})
        h.setdefault("price", 0.0)
        h.setdefault("price_auto", True)
        for k, (lo, hi) in HOME_BOUNDS.items():
            v = h.get(k, core.MORTGAGE_DEFAULTS.get(k, lo))
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = lo
            h[k] = min(max(v, lo), hi)
        return h

    def hm_recalc(*_):
        h = hcfg()
        gross = store.gross_monthly()
        other = store.debt_payments_monthly()
        a = core.affordability(gross_monthly=gross, down=h["down"], other_debts=other,
                               apr=h["apr"], years=h["years"], tax_rate=h["tax_rate"],
                               ins_annual=h["insurance"], pmi_rate=h["pmi_rate"],
                               hoa=h["hoa"], front=h["front"], back=h["back"])
        budget = a["budget"]
        if h.get("price_auto") or h.get("price", 0) <= 0:
            price = a["price"]
            if HM.get("set_price"):
                HM["set_price"](price)
            h["price"] = price
        else:
            price = h["price"]
        det = core.piti(price, h["down"], h["apr"], h["years"], h["tax_rate"],
                        h["insurance"], h["pmi_rate"], h["hoa"])
        p = core.pmi_timeline(price, h["down"], h["apr"], h["years"], h["pmi_rate"])

        HM["price"].config(text=M0(price))
        HM["pay"].config(text=M0(det["total"]) + "/mo")
        HM["ltv"].config(text=f"{det['ltv']*100:.1f}% loan-to-value on a "
                              f"{M0(det['loan'])} loan")
        over = det["total"] - budget
        if over > 1:
            HM["binding"].config(
                text=f"OVER what you'd qualify for by {M(over)}/mo — "
                     f"lenders cap you at {M(budget)}", fg=C["warnink"])
            HM["verdict"].config(
                text=f"A lender would say no. Your ceiling is {M0(a['price'])} "
                     f"at this down payment, set by {a['binding']}.",
                fg=C["bad"], bg=C["badbg"])
            if HM.get("verdict_box"):
                HM["verdict_box"].config(bg=C["badbg"], highlightbackground=C["bad"])
        else:
            HM["binding"].config(
                text=f"within the {M(budget)}/mo a lender would allow "
                     f"({M(abs(over))} of room)", fg=C["herosub"])
            HM["verdict"].config(
                text=f"This fits. Your maximum at this down payment is "
                     f"{M0(a['price'])}, limited by {a['binding']}.",
                fg=C["okink"], bg=C["goodbg"])
            if HM.get("verdict_box"):
                HM["verdict_box"].config(bg=C["goodbg"], highlightbackground=C["good"])

        for k, v in (("pi", det["pi"]), ("tax", det["tax"]), ("ins", det["ins"]),
                     ("pmi", det["pmi"]), ("hoa", det["hoa"]),
                     ("total", det["total"])):
            if k in HM:
                HM[k].config(text=M(v))
        tot = max(det["total"], 1)
        for k in ("pi", "tax", "ins", "pmi", "hoa"):
            bar = HM.get("bar_" + k)
            if bar:
                bar.config(width=max(int(det[k] / tot * 560), 1) if det[k] > 0 else 1)

        if p["applies"]:
            HM["pmi_head"].config(text=f"PMI applies — {M(p['monthly'])} a month",
                                  fg=C["bad"])
            HM["pmi_detail"].config(text=(
                f"You'd be putting {h['down']/max(price,1)*100:.1f}% down, which is "
                f"under 20%, so the lender adds PMI.\n\n"
                f"• You can ASK to cancel at month {p['request_month']} "
                f"(~{month_label(store.settings['start'], p['request_month'])}), when "
                f"the balance hits 80% of the purchase price.\n"
                f"• They must cancel it AUTOMATICALLY at month {p['auto_month']}, "
                f"at 78%.\n"
                f"• Either way it must end by month {p['midpoint_month']}, the halfway "
                f"point of the loan.\n\n"
                f"Asking instead of waiting saves about {M(p['saved_by_asking'])}. "
                f"Total PMI if you wait: {M(p['total_if_auto'])}.\n\n"
                f"To skip PMI entirely on a {M0(price)} house you'd need "
                f"{M0(core.down_to_avoid_pmi(price))} down."))
        else:
            HM["pmi_head"].config(text="No PMI — you're at 20% down or better",
                                  fg=C["okink"])
            HM["pmi_detail"].config(text=(
                "Your down payment is at least 20% of the price, so the lender "
                "doesn't require mortgage insurance. That's the whole reason 20% is "
                "the number everyone repeats."))

        # the price at which PMI disappears for this down payment
        cliff = h["down"] / 0.20 if h["down"] > 0 else 0.0
        life = core.amortization(det["loan"], h["apr"], h["years"]) if det["loan"] > 0 else None
        if life:
            HM["life"].config(
                text=(f"Over the full {int(h['years'])} years you'd pay "
                      f"{M(life['interest'])} in interest on a {M0(det['loan'])} loan — "
                      f"{M(det['loan'] + life['interest'])} for a {M0(price)} house."))
        else:
            HM["life"].config(text="No loan at this price and down payment.")
        if cliff > 0 and price > cliff:
            extra = det["pmi"]
            HM["cliff"].config(
                text=(f"{M0(cliff)} is the most you can pay WITHOUT PMI at this "
                      f"down payment. You're {M0(price - cliff)} above it, which is "
                      f"costing {M(extra)} a month."),
                fg=C["warnink"])
        elif cliff > 0 and price <= cliff:
            HM["cliff"].config(
                text=(f"You're at or under {M0(cliff)}, the PMI line for this down "
                      f"payment — no mortgage insurance. One dollar more and it "
                      f"starts."), fg=C["okink"])
        else:
            HM["cliff"].config(text="Add a down payment to see the PMI threshold.",
                               fg=C["muted"])

        g = [x for x in store.goals
             if "down" in x.name.lower() or "hous" in x.name.lower()]
        saved = sum(x.saved for x in g)
        gap = max(h["down"] - saved, 0)
        if gap > 0:
            fc = max(store.free_cash(1), 0)
            n = (f"about {gap/fc:.0f} months at your current free cash" if fc > 0
                 else "and right now there is nothing free each month to save it with")
            HM["gap"].config(text=f"You have {M(saved)} saved toward this. You need "
                                  f"{M(gap)} more — {n}.", fg=C["bad"])
        else:
            HM["gap"].config(text=f"You have {M(saved)} saved — enough for this "
                                  f"down payment.", fg=C["okink"])

    def hm_row(parent, label, key, lo, hi, fmt, step=None, pct=False, note=""):
        row = tk.Frame(parent, bg=C["card"])
        row.pack(fill="x", padx=14, pady=3)
        tk.Label(row, text=label, bg=C["card"], fg=C["ink"], font=(FONT, 9),
                 width=20, anchor="w").pack(side="left")
        mult = 100.0 if pct else 1.0

        def on(v):
            hcfg()[key] = float(v) / mult
            if key == "price":
                hcfg()["price_auto"] = False
            hm_recalc()
        ctl, setv, _ = num_control(row, hcfg()[key] * mult, lo, hi, on,
                                   fmt=fmt, step=step)
        ctl.pack(side="left")
        if note:
            tk.Label(row, text=note, bg=C["card"], fg=C["muted"],
                     font=(FONT, 8)).pack(side="left", padx=8)
        return setv

    def refresh_home():
        for w in hm_body.winfo_children():
            w.destroy()
        HM.clear()
        h = hcfg()

        hero = tk.Frame(hm_body, bg=SERIES[0])
        hero.pack(fill="x")
        hi = tk.Frame(hero, bg=SERIES[0])
        hi.pack(fill="x", padx=20, pady=14)
        tk.Label(hi, text="HOUSE PRICE YOU'RE LOOKING AT", bg=SERIES[0],
                 fg=C["herosub"], font=(FONT, 8, "bold")).pack(anchor="w")
        HM["price"] = tk.Label(hi, text="—", bg=SERIES[0], fg="#ffffff",
                               font=(FONT, 32, "bold"))
        HM["price"].pack(anchor="w")
        HM["pay"] = tk.Label(hi, text="", bg=SERIES[0], fg="#ffffff", font=(FONT, 13))
        HM["pay"].pack(anchor="w")
        HM["binding"] = tk.Label(hi, text="", bg=SERIES[0], fg=C["herosub"],
                                 font=(FONT, 9))
        HM["binding"].pack(anchor="w")
        HM["ltv"] = tk.Label(hi, text="", bg=SERIES[0], fg=C["herosub"], font=(FONT, 9))
        HM["ltv"].pack(anchor="w")

        vb = tk.Frame(hm_body, bg=C["goodbg"], highlightthickness=1,
                      highlightbackground=C["good"])
        vb.pack(fill="x", padx=14, pady=(10, 2))
        HM["verdict_box"] = vb
        HM["verdict"] = tk.Label(vb, text="", bg=C["goodbg"], fg=C["okink"],
                                 font=(FONT, 10, "bold"), wraplength=840,
                                 justify="left", anchor="w")
        HM["verdict"].pack(fill="x", padx=12, pady=9)

        # income caveat
        cav = tk.Frame(hm_body, bg=C["warnbg"], highlightthickness=1,
                       highlightbackground=C["warn"])
        cav.pack(fill="x", padx=14, pady=(12, 6))
        est = " (estimated from your take-home — replace it with the gross from a paystub)" \
            if store.gross_is_estimated() else ""
        tk.Label(cav, text=f"Lenders qualify you on GROSS pay, not take-home. "
                           f"Using {M(store.gross_monthly())}/month{est}.",
                 bg=C["warnbg"], fg=C["warnink"], font=(FONT, 9), wraplength=780,
                 justify="left").pack(side="left", padx=12, pady=8)

        def set_gross():
            v = simpledialog.askstring("Gross monthly income",
                                       "Gross (pre-tax) monthly income:",
                                       initialvalue=f"{store.gross_monthly():.2f}")
            if v is None:
                return
            try:
                store.settings["gross_monthly"] = float(
                    str(v).replace("$", "").replace(",", ""))
            except ValueError:
                return
            save()
            refresh_home()
        ttk.Button(cav, text="Set gross income…", command=set_gross).pack(
            side="right", padx=10)

        # ---- controls ----
        ctl = tk.Frame(hm_body, bg=C["card"], highlightthickness=1,
                       highlightbackground=C["line"])
        ctl.pack(fill="x", padx=14, pady=6)
        tk.Frame(ctl, bg=SERIES[0], height=4).pack(fill="x")
        tk.Label(ctl, text="ADJUST THESE", bg=C["card"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        prow = tk.Frame(ctl, bg=C["card"])
        prow.pack(fill="x", padx=14, pady=(2, 6))
        tk.Label(prow, text="House price", bg=C["card"], fg=C["ink"],
                 font=(FONT, 10, "bold"), width=20, anchor="w").pack(side="left")

        def on_price(v):
            hcfg()["price"] = float(v)
            hcfg()["price_auto"] = False
            hm_recalc()
        pctl, setp, _ = num_control(prow, hcfg().get("price") or 200000, 0, 900000,
                                    on_price, fmt=lambda v: f"{v:,.0f}", step=5000)
        pctl.pack(side="left")
        HM["set_price"] = setp

        def use_max():
            hcfg()["price_auto"] = True
            hm_recalc()
            save()
        tk.Button(prow, text="use my max", relief="flat", bg=C["wash"], fg=SERIES[0],
                  font=(FONT, 8, "bold"), cursor="hand2", padx=8,
                  command=use_max).pack(side="left", padx=10)

        ttk.Separator(ctl).pack(fill="x", padx=14, pady=6)
        hm_row(ctl, "Down payment", "down", 0, 200000, lambda v: f"{v:,.0f}", step=1000)
        hm_row(ctl, "Interest rate (APR)", "apr", 3.0, 12.0,
               lambda v: f"{v:.2f}", step=0.125, pct=True, note="%")
        hm_row(ctl, "Loan term (years)", "years", 8, 40, lambda v: f"{v:.0f}", step=5)
        hm_row(ctl, "Property tax rate", "tax_rate", 0.0, 3.0,
               lambda v: f"{v:.2f}", step=0.05, pct=True, note="%")
        hm_row(ctl, "Home insurance /yr", "insurance", 0, 12000,
               lambda v: f"{v:,.0f}", step=100)
        hm_row(ctl, "PMI rate", "pmi_rate", 0.0, 2.0, lambda v: f"{v:.2f}",
               step=0.05, pct=True, note="%")
        hm_row(ctl, "HOA dues /mo", "hoa", 0, 1000, lambda v: f"{v:,.0f}", step=25)
        tk.Label(ctl, text="Type in any box and press Enter for an exact number — "
                           "the sliders are just for feel.",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(
                     anchor="w", padx=14, pady=(6, 0))
        tk.Label(ctl, text="Today: 30-year fixed averages 6.67%, 15-year 5.96% "
                           "(Freddie Mac, Aug 13 2026). Wentzville property tax runs "
                           "about 1.31%; Missouri home insurance averages $3,940/yr. "
                           "Conventional PMI runs 0.30%–1.50% of the loan per year.",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8), wraplength=860,
                 justify="left").pack(anchor="w", padx=14, pady=(6, 12))

        # ---- payment breakdown ----
        pb = tk.Frame(hm_body, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"])
        pb.pack(fill="x", padx=14, pady=6)
        tk.Label(pb, text="WHAT THE MONTHLY PAYMENT IS MADE OF", bg=C["card"],
                 fg=C["muted"], font=(FONT, 8, "bold")).pack(anchor="w", padx=14,
                                                             pady=(12, 6))
        for key, lab, col in (("pi", "Principal & interest", SERIES[0]),
                              ("tax", "Property tax", SERIES[1]),
                              ("ins", "Home insurance", SERIES[3]),
                              ("pmi", "PMI", SERIES[7]),
                              ("hoa", "HOA dues", SERIES[5])):
            row = tk.Frame(pb, bg=C["card"])
            row.pack(fill="x", padx=14, pady=2)
            tk.Frame(row, bg=col, width=10, height=10).pack(side="left", padx=(0, 8))
            tk.Label(row, text=lab, bg=C["card"], fg=C["ink"], font=(FONT, 9),
                     width=20, anchor="w").pack(side="left")
            HM[key] = tk.Label(row, text="—", bg=C["card"], fg=C["ink"],
                               font=(FONT, 9, "bold"), width=11, anchor="e")
            HM[key].pack(side="left")
            wrap = tk.Frame(row, bg=C["line"], height=10, width=560)
            wrap.pack(side="left", padx=12)
            HM["bar_" + key] = tk.Frame(wrap, bg=col, height=10, width=1)
            HM["bar_" + key].pack(side="left")
        ttk.Separator(pb).pack(fill="x", padx=14, pady=6)
        trow = tk.Frame(pb, bg=C["card"])
        trow.pack(fill="x", padx=14, pady=(0, 12))
        tk.Frame(trow, bg=C["ink"], width=10, height=10).pack(side="left", padx=(0, 8))
        tk.Label(trow, text="TOTAL", bg=C["card"], fg=C["ink"],
                 font=(FONT, 10, "bold"), width=20, anchor="w").pack(side="left")
        HM["total"] = tk.Label(trow, text="—", bg=C["card"], fg=C["ink"],
                               font=(FONT, 11, "bold"), width=11, anchor="e")
        HM["total"].pack(side="left")

        cf = tk.Frame(hm_body, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"])
        cf.pack(fill="x", padx=14, pady=6)
        tk.Frame(cf, bg=SERIES[3], height=4).pack(fill="x")
        tk.Label(cf, text="THE TWO NUMBERS PEOPLE MISS", bg=C["card"], fg=C["muted"],
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        HM["cliff"] = tk.Label(cf, text="", bg=C["card"], fg=C["ink2"], font=(FONT, 9),
                               wraplength=840, justify="left", anchor="w")
        HM["cliff"].pack(fill="x", padx=14)
        HM["life"] = tk.Label(cf, text="", bg=C["card"], fg=C["ink2"], font=(FONT, 9),
                              wraplength=840, justify="left", anchor="w")
        HM["life"].pack(fill="x", padx=14, pady=(6, 12))

        # ---- down payment reality ----
        gapf = tk.Frame(hm_body, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["line"])
        gapf.pack(fill="x", padx=14, pady=6)
        tk.Label(gapf, text="CAN YOU ACTUALLY MAKE THE DOWN PAYMENT?", bg=C["card"],
                 fg=C["muted"], font=(FONT, 8, "bold")).pack(anchor="w", padx=14,
                                                             pady=(12, 4))
        HM["gap"] = tk.Label(gapf, text="", bg=C["card"], fg=C["ink"], font=(FONT, 10),
                             wraplength=840, justify="left", anchor="w")
        HM["gap"].pack(fill="x", padx=14, pady=(0, 12))

        # ---- PMI ----
        pf = tk.Frame(hm_body, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"])
        pf.pack(fill="x", padx=14, pady=6)
        tk.Frame(pf, bg=SERIES[7], height=4).pack(fill="x")
        HM["pmi_head"] = tk.Label(pf, text="", bg=C["card"], fg=C["ink"],
                                  font=(FONT, 13, "bold"), anchor="w")
        HM["pmi_head"].pack(fill="x", padx=14, pady=(12, 4))
        HM["pmi_detail"] = tk.Label(pf, text="", bg=C["card"], fg=C["ink2"],
                                    font=(FONT, 9), wraplength=860, justify="left",
                                    anchor="w")
        HM["pmi_detail"].pack(fill="x", padx=14, pady=(0, 10))

        expl = (
            "WHAT PMI ACTUALLY IS\n"
            "Private mortgage insurance protects the LENDER, not you. If you put down "
            "less than 20%, they take on more risk, and PMI is what you pay to cover it. "
            "You get nothing from it except the ability to buy sooner.\n\n"
            "WHEN IT KICKS IN\n"
            "Any conventional loan above 80% loan-to-value — under 20% down. It's added "
            "to your monthly payment and typically costs 0.30% to 1.50% of the loan "
            "per year. A weaker credit score and a smaller down payment both push you "
            "toward the higher end.\n\n"
            "WHEN IT GOES AWAY (conventional loans, Homeowners Protection Act)\n"
            "  · At 80% of the ORIGINAL price you can request cancellation in writing. "
            "You need to be current on payments, have no second lien, and may need an "
            "appraisal showing the value hasn't dropped.\n"
            "  · At 78% the servicer must cancel it automatically — you don't have to ask, "
            "but you do have to be current.\n"
            "  · At the midpoint of the loan (year 15 of a 30-year) it must end "
            "regardless of the balance.\n"
            "Asking at 80% rather than waiting for 78% is free money. The app shows both "
            "dates above.\n\n"
            "FHA IS DIFFERENT — AND WORSE ON THIS POINT\n"
            "FHA loans let you in with 3.5% down, but charge 1.75% of the loan upfront "
            "plus about 0.55% a year. If you put down less than 10%, that annual premium "
            "lasts the ENTIRE life of the loan — it never falls off. The only way out is "
            "refinancing into a conventional loan once you have equity. With 10% or more "
            "down it drops after 11 years.\n\n"
            "THE PRACTICAL TAKE\n"
            "PMI is not automatically a mistake. Waiting years to reach 20% while rents "
            "and prices rise can cost more than the premium. But it is a real monthly "
            "cost with no benefit to you, so know the number, know both cancellation "
            "dates, and put the reminder in your calendar the day you close.")
        tk.Label(pf, text=expl, bg=C["card"], fg=C["ink2"], font=(FONT, 9),
                 wraplength=860, justify="left", anchor="w").pack(
                     fill="x", padx=14, pady=(0, 14))

        src = tk.Frame(hm_body, bg=C["bg"])
        src.pack(fill="x", padx=16, pady=(6, 24))
        tk.Label(src, text="Figures gathered August 15, 2026 — Freddie Mac PMMS "
                           "(rates), CFPB (PMI cancellation rules), Ownwell "
                           "(Wentzville property tax), Insure.com (Missouri home "
                           "insurance). Rates move; re-check before you rely on any of "
                           "this. This is a planning tool, not lending advice — a real "
                           "pre-approval is the only number that counts.",
                 bg=C["bg"], fg=C["muted"], font=(FONT, 8), wraplength=880,
                 justify="left").pack(anchor="w")
        hm_recalc()

    REFRESH.append(refresh_home)

    # ═══ PLAN ═════════════════════════════════════════════════════════════
    tab_p = ttk.Frame(nb, padding=0)
    nb.add(tab_p, text="  Plan  ")

    p_outer = tk.Canvas(tab_p, bg=C["bg"], highlightthickness=0)
    p_sb = ttk.Scrollbar(tab_p, orient="vertical", command=p_outer.yview)
    p_body = tk.Frame(p_outer, bg=C["bg"])
    p_body.bind("<Configure>",
                lambda e: p_outer.configure(scrollregion=p_outer.bbox("all")))
    p_win = p_outer.create_window((0, 0), window=p_body, anchor="nw")
    p_outer.bind("<Configure>", lambda e: p_outer.itemconfig(p_win, width=e.width))
    p_outer.configure(yscrollcommand=p_sb.set)
    p_outer.pack(side="left", fill="both", expand=True)
    p_sb.pack(side="right", fill="y")

    def refresh_plan():
        for w in p_body.winfo_children():
            w.destroy()
        r = project(store)
        start = store.settings["start"]
        base = project_with_cuts(store, {})
        names = [d.name for d in store.debts if d.include and d.balance > 0]

        # ── headline ──────────────────────────────────────────────────────
        head = tk.Frame(p_body, bg=C["bg"])
        head.pack(fill="x", padx=16, pady=(16, 4))
        if r["months"]:
            big = month_label(start, r["months"])
            sub = f"{r['months']} months from now"
            col = C["good"]
        else:
            big, sub, col = "Not on track", "there isn't enough free cash each month", C["bad"]
        tk.Label(head, text="Debt-free", bg=C["bg"], fg=C["muted"],
                 font=(FONT, 9, "bold")).pack(anchor="w")
        tk.Label(head, text=big, bg=C["bg"], fg=col,
                 font=(FONT, 30, "bold")).pack(anchor="w")
        tk.Label(head, text=sub, bg=C["bg"], fg=C["ink2"], font=(FONT, 10)).pack(anchor="w")

        # ── stat tiles ────────────────────────────────────────────────────
        tiles = tk.Frame(p_body, bg=C["bg"])
        tiles.pack(fill="x", padx=12, pady=10)
        # only meaningful if the no-cuts baseline actually clears; otherwise the
        # figure runs away as balances compound forever.
        saved = (max(0.0, base["interest"] - r["interest"])
                 if (base["months"] and r["months"]
                     and base["interest"] is not None
                     and r["interest"] is not None) else 0.0)
        cards = [
            ("You owe", M0(sum(d.balance for d in store.debts if d.include)),
             f"{M(sum(d.balance*d.apr/12 for d in store.debts if d.include))}/mo in interest"),
            ("Paying", M0(store.free_cash(1)) + "/mo",
             f"{M0(store.free_cash(12))}/mo once the 401(k) loan ends"),
            ("Interest you'll pay",
             M0(r["interest"]) if (r["months"] and r["interest"] is not None) else "—",
             "on the current plan"),
            ("Trimming saves", M0(saved) if saved > 1 else "—",
             "vs. no cuts at all" if saved > 1 else
             ("nothing to compare yet" if r["months"] else "trim something to get a date")),
        ]
        for i, (lab, val, note) in enumerate(cards):
            c = tk.Frame(tiles, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["line"])
            c.grid(row=0, column=i, sticky="ew", padx=4)
            tiles.columnconfigure(i, weight=1)
            tk.Label(c, text=lab.upper(), bg=C["card"], fg=C["muted"],
                     font=(FONT, 8, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
            tk.Label(c, text=val, bg=C["card"], fg=C["ink"],
                     font=(FONT, 19, "bold")).pack(anchor="w", padx=12)
            tk.Label(c, text=note, bg=C["card"], fg=C["ink2"], font=(FONT, 8),
                     wraplength=190, justify="left").pack(anchor="w", padx=12, pady=(0, 10))

        # ── warnings ──────────────────────────────────────────────────────
        for wmsg in r["warnings"]:
            bx = tk.Frame(p_body, bg=C["badbg"], highlightthickness=1,
                          highlightbackground=C["bad"])
            bx.pack(fill="x", padx=16, pady=4)
            tk.Label(bx, text="!", bg=C["badbg"], fg=C["bad"],
                     font=(FONT, 12, "bold")).pack(side="left", padx=(12, 6), pady=10)
            tk.Label(bx, text=wmsg, bg=C["badbg"], fg=C["ink"], font=(FONT, 9),
                     wraplength=860, justify="left").pack(side="left", pady=10, padx=(0, 12))
        if not r["warnings"] and r["months"]:
            bx = tk.Frame(p_body, bg=C["goodbg"], highlightthickness=1,
                          highlightbackground=C["good"])
            bx.pack(fill="x", padx=16, pady=4)
            tk.Label(bx, text="Every balance clears, and the 0% deadline is met.",
                     bg=C["goodbg"], fg=C["okink"], font=(FONT, 9)).pack(
                         anchor="w", padx=12, pady=9)

        # ── chart ─────────────────────────────────────────────────────────
        if r["rows"]:
            cw = tk.Frame(p_body, bg=C["card"], highlightthickness=1,
                          highlightbackground=C["line"])
            cw.pack(fill="x", padx=16, pady=(10, 6))
            tk.Label(cw, text="What you owe, month by month", bg=C["card"], fg=C["ink"],
                     font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
            leg = tk.Frame(cw, bg=C["card"])
            leg.pack(anchor="w", padx=14, pady=6)
            for i, n in enumerate(names):
                tk.Frame(leg, bg=SERIES[i % len(SERIES)], width=10, height=10).pack(
                    side="left", padx=(0 if i == 0 else 12, 5))
                tk.Label(leg, text=n, bg=C["card"], fg=C["ink2"],
                         font=(FONT, 8)).pack(side="left")
            ch = tk.Canvas(cw, height=260, bg=C["card"], highlightthickness=0)
            ch.pack(fill="x", padx=14, pady=(0, 14))
            ch.bind("<Configure>",
                    lambda e, rows=r["rows"], nm=names: draw_chart(e.widget, rows, nm))
            # draw once right away too, in case <Configure> doesn't fire on first map
            ch.after(60, lambda c=ch, rows=r["rows"], nm=names: draw_chart(c, rows, nm))

        # ── payoff order ──────────────────────────────────────────────────
        ordr = tk.Frame(p_body, bg=C["card"], highlightthickness=1,
                        highlightbackground=C["line"])
        ordr.pack(fill="x", padx=16, pady=6)
        tk.Label(ordr, text="The order, and why", bg=C["card"], fg=C["ink"],
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
        seq = sorted([d for d in store.debts if d.include and d.balance > 0],
                     key=lambda d: r["cleared"].get(d.name, 999))
        for i, d in enumerate(seq):
            row = tk.Frame(ordr, bg=C["card"])
            row.pack(fill="x", padx=14, pady=2)
            tk.Label(row, text=str(i + 1), bg=C["wash"], fg=C["ink"], width=3,
                     font=(FONT, 9, "bold")).pack(side="left", padx=(0, 10))
            tk.Label(row, text=d.name, bg=C["card"], fg=C["ink"], width=18, anchor="w",
                     font=(FONT, 9, "bold")).pack(side="left")
            tk.Label(row, text=M(d.balance), bg=C["card"], fg=C["ink2"], width=11,
                     anchor="e", font=(FONT, 9)).pack(side="left")
            tk.Label(row, text=f"{d.apr*100:.2f}%", bg=C["card"], fg=C["ink2"], width=9,
                     anchor="e", font=(FONT, 9)).pack(side="left")
            cm = r["cleared"].get(d.name)
            gone = f"gone {month_label(start, cm)}" if cm else "not cleared"
            tk.Label(row, text=gone, bg=C["card"],
                     fg=C["okink"] if cm else C["bad"], width=16, anchor="w",
                     font=(FONT, 9)).pack(side="left", padx=(12, 0))
            if d.promo_until:
                why = f"0% until {d.promo_until} — reserved monthly so it lands at zero in time"
            elif i == 0:
                why = "highest rate, so it bleeds you fastest"
            elif d.apr < 0.10:
                why = "cheap money — minimums only until the rest is gone"
            else:
                why = "next highest rate"
            tk.Label(row, text=why, bg=C["card"], fg=C["muted"], font=(FONT, 8),
                     anchor="w").pack(side="left", padx=(8, 0))
        tk.Frame(ordr, bg=C["card"], height=8).pack()

        # ── what to pay each month ────────────────────────────────────────
        if r["rows"]:
            pw = tk.Frame(p_body, bg=C["card"], highlightthickness=1,
                          highlightbackground=C["line"])
            pw.pack(fill="both", expand=True, padx=16, pady=6)
            tk.Label(pw, text="What to send, and where", bg=C["card"], fg=C["ink"],
                     font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
            cols = ["month"] + names + ["left"]
            tv = ttk.Treeview(pw, columns=cols, show="headings",
                              height=min(len(r["rows"]) + 1, 16))
            tv.heading("month", text="Month")
            tv.column("month", width=90, anchor="w")
            for n in names:
                tv.heading(n, text=n[:14])
                tv.column(n, width=105, anchor="e")
            tv.heading("left", text="Still owed")
            tv.column("left", width=105, anchor="e")
            for row in r["rows"]:
                vals = [row["label"]]
                for n in names:
                    p = row["paid"].get(n, 0)
                    b = row["bal"].get(n, 0)
                    vals.append("paid off" if b <= 0.005 and p <= 0.005
                                else (M0(p) if p > 0.5 else "—"))
                vals.append(M0(row["total"]) if row["total"] > 0.5 else "DEBT FREE")
                tv.insert("", "end", values=vals)
            tv.pack(fill="both", expand=True, padx=14, pady=(0, 6))
            tk.Label(pw, text="Each number is what to pay that card that month. "
                              "Set the minimums to autopay and send the rest by hand.",
                     bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(
                         anchor="w", padx=14, pady=(0, 12))

        # ── how you're actually doing ─────────────────────────────────────
        actual = store.actual_debt_payments()
        real = {m: v for m, v in actual.items() if v > 0}
        if real:
            pv = tk.Frame(p_body, bg=C["card"], highlightthickness=1,
                          highlightbackground=C["line"])
            pv.pack(fill="x", padx=16, pady=6)
            tk.Frame(pv, bg=SERIES[2], height=4).pack(fill="x")
            tk.Label(pv, text="WHAT YOU ACTUALLY SENT TO CARDS", bg=C["card"],
                     fg=C["muted"], font=(FONT, 8, "bold")).pack(
                         anchor="w", padx=14, pady=(10, 2))
            target = max(store.free_cash(1), 0.0)
            tk.Label(pv, text=f"The plan asks for {M(target)} a month. "
                             f"Here is what your statements show going out:",
                     bg=C["card"], fg=C["ink2"], font=(FONT, 9)).pack(
                         anchor="w", padx=14)
            for m in sorted(real):
                v = real[m]
                row = tk.Frame(pv, bg=C["card"])
                row.pack(fill="x", padx=14, pady=2)
                tk.Label(row, text=m, bg=C["card"], fg=C["ink"], width=10,
                         anchor="w", font=(FONT, 9)).pack(side="left")
                tk.Label(row, text=M(v), bg=C["card"], fg=C["ink"], width=11,
                         anchor="e", font=(FONT, 9, "bold")).pack(side="left")
                widest = max(real.values()) or 1
                wrap = tk.Frame(row, bg=C["line"], height=10, width=420)
                wrap.pack(side="left", padx=12)
                tk.Frame(wrap, bg=SERIES[2] if v >= target else SERIES[3],
                         height=10, width=max(int(v / widest * 420), 2)).pack(side="left")
                if target > 0:
                    diff = v - target
                    tk.Label(row,
                             text=("+" + M(diff) if diff >= 0 else "−" + M(-diff))
                                  + " vs plan",
                             bg=C["card"], fg=C["okink"] if diff >= 0 else C["bad"],
                             font=(FONT, 8)).pack(side="left")
            avg = sum(real.values()) / len(real)
            tk.Label(pv, text=f"Average {M(avg)}/month across {len(real)} month(s) "
                             "of statements.",
                     bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(
                         anchor="w", padx=14, pady=(6, 12))

        # ── scenarios ─────────────────────────────────────────────────────
        sw = tk.Frame(p_body, bg=C["card"], highlightthickness=1,
                      highlightbackground=C["line"])
        sw.pack(fill="x", padx=16, pady=(6, 20))
        tk.Label(sw, text="If you trimmed more", bg=C["card"], fg=C["ink"],
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(sw, text="Each row spreads the trim across your flexible categories, "
                          "leaving groceries alone.",
                 bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", padx=14)
        stv = ttk.Treeview(sw, columns=("cut", "pay", "when", "int", "ok"),
                           show="headings", height=9)
        for c, t, wd in (("cut", "Trim per month", 130), ("pay", "Toward debt", 120),
                         ("when", "Debt-free", 120), ("int", "Interest", 110),
                         ("ok", "0% deadline met", 130)):
            stv.heading(c, text=t)
            stv.column(c, width=wd, anchor="e" if c in ("cut", "pay", "int") else "center")
        cur_cut = store.cuts_total()
        for s in scenarios(store):
            mark = "  ← you" if abs(s["cut"] - cur_cut) < 50 else ""
            stv.insert("", "end", values=(
                M0(s["cut"]) + mark, M0(s["toward_debt"]), s["when"],
                M(s["interest"]) if s.get("interest") is not None else "—",
                "yes" if s["ok"] else "NO"))
        stv.pack(fill="x", padx=14, pady=(6, 14))

    def draw_chart(cv, rows, names):
        cv.delete("all")
        W = max(cv.winfo_width(), 400)
        H = 260
        ml, mr, mt, mb = 58, 14, 14, 34
        iw, ih = W - ml - mr, H - mt - mb
        peak = max([r["total"] for r in rows] + [1])
        step = 10 ** (len(str(int(peak))) - 1) or 1
        top = (int(peak / step) + 1) * step
        y = lambda v: mt + ih - (v / top) * ih
        for g in range(0, int(top) + 1, max(int(top // 5), 1)):
            cv.create_line(ml, y(g), W - mr, y(g), fill=C["line"])
            cv.create_text(ml - 8, y(g), text=f"${g:,.0f}", anchor="e",
                           fill=C["muted"], font=(FONT, 8))
        n = len(rows)
        slot = iw / max(n, 1)
        bw = min(56, slot * 0.6)
        for i, row in enumerate(rows):
            cx = ml + slot * i + slot / 2
            acc = 0.0
            for j, nm in enumerate(names):
                v = row["bal"].get(nm, 0)
                if v <= 0.5:
                    continue
                y0, y1 = y(acc + v), y(acc)
                cv.create_rectangle(cx - bw / 2, y0, cx + bw / 2, max(y1 - 2, y0),
                                    fill=SERIES[j % len(SERIES)], outline=C["card"])
                acc += v
            if row["total"] > 0.5:
                cv.create_text(cx, y(row["total"]) - 9, text=M0(row["total"]),
                               fill=C["ink"], font=(FONT, 8, "bold"))
            else:
                cv.create_text(cx, y(0) - 12, text="DONE", fill=C["okink"],
                               font=(FONT, 8, "bold"))
            if n <= 18 or i % 2 == 0:
                cv.create_text(cx, H - mb + 14, text=row["label"].replace(" 20", " '"),
                               fill=C["ink2"], font=(FONT, 8))
        cv.create_line(ml, y(0), W - mr, y(0), fill=C["axis"])

    REFRESH.append(refresh_plan)

    def on_tab(_e=None):
        try:
            refresh_dash()
            refresh_plan()
        except Exception:
            traceback.print_exc()
    nb.bind("<<NotebookTabChanged>>", on_tab)

    refresh_all()

    def on_close():
        try:
            store.settings["window"] = root.geometry().split("+")[0]
            store.settings["last_tab"] = nb.index(nb.select())
        except Exception:
            pass
        save()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        nb.select(int(store.settings.get("last_tab", 0)))
    except Exception:
        pass

    # anything the app had to tell you before you start clicking
    notices = [m for m in (load_msg, start_msg) if m]
    if pruned:
        notices.append(f"{pruned} statement file(s) in the scan list no longer "
                       "exist and were forgotten.")
    if notices:
        root.after(300, lambda: messagebox.showinfo(
            "Before you start", "\n\n".join(notices)))

    root.mainloop()
    return 0


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if "--report" in args:
        s = Store(DATA_PATH)
        s.load()
        print(report(s))
        return 0
    return launch()


if __name__ == "__main__":
    sys.exit(main())
