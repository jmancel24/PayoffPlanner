"""
core.py — statement parsing, categorization, and payoff math.

Pure logic, no GUI. Everything here is exercised by `python finplan.py --selftest`.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime

APP_NAME = "Payoff Planner"
RULES_VERSION = 3   # bump when DEFAULT_RULES changes so saved files pick up fixes
DATA_FILE = "finplan_data.json"

# ───────────────────────────────────────────────────────────────────────────
#  MODEL
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class Txn:
    date: str          # ISO yyyy-mm-dd
    desc: str
    amount: float      # positive = money out, negative = money in
    account: str       # "AMEX Gold", "Apple Card", "Checking", ...
    category: str = "Uncategorized"
    kind: str = "spend"   # spend | bill | debt_payment | income | transfer
    source: str = ""      # file it came from
    txn_id: str = ""
    exclude: bool = False   # one-time charge: keep it visible, drop from the monthly baseline
    note: str = ""
    manual: bool = False    # you set this category by hand — auto-rules leave it alone

    seq: int = 0            # 1st, 2nd, 3rd identical row within one statement

    def key(self):
        return (f"{self.date}|{self.account}|{round(self.amount,2)}"
                f"|{self.desc[:40].upper()}|{self.seq}")


@dataclass
class Debt:
    name: str
    balance: float
    apr: float           # 0.2849
    minimum: float
    promo_until: str = ""      # ISO date; blank = no promo
    promo_apr_after: float = 0.0
    include: bool = True
    limit: float = 0.0         # credit limit, for utilization; 0 = unknown
    check: int = 0             # which paycheck the card payment comes out of
    balance_asof: str = ""     # ISO date this balance is true as of
    balance_source: str = ""   # "statement" | "manual" | ""
    last4: str = ""            # last 4 of the account number — tells two cards
                               # from the SAME issuer apart


@dataclass
class Account:
    """
    A spending account — a debit card or envelope you fund on payday and spend
    from. Categories and bills are assigned to it; the app works out what to
    move across from each paycheck.
    """
    name: str
    categories: list = field(default_factory=list)
    bills: list = field(default_factory=list)
    split: float = 50.0        # % of the monthly total taken from the 1st check
    note: str = ""
    color: int = 0


@dataclass
class Bill:
    name: str
    amount: float
    day: str = ""
    paid_from: str = ""
    active: bool = True
    check: int = 0     # 0 = share it by the account's split, 1 = 1st check, 2 = 2nd


DEFAULT_CATEGORIES = [
    "Groceries & household", "Restaurants & coffee", "Bars & liquor",
    "Gas & convenience", "Amazon & shopping", "Dispensary",
    "Cash, ATM & P2P", "Travel & entertainment", "Subscriptions",
    "Medical", "Pets", "Kids", "Home & garden", "Other",
]

# Substring -> (category, kind). First match wins; checked in order.
DEFAULT_RULES = [
    # ---- income / transfers ----
    (r"PAYROLL|Data2 Incorporat", "Income", "income"),
    (r"DATA2 DYNAMICS|PAYABLES", "Reimbursement", "income"),
    (r"ATM Deposit|Point Of Sale Deposit|External Deposit|Refund|GWIC|WELLABE",
     "Deposit & refunds", "income"),
    # ---- debt payments ----
    (r"AMEX EPAYMENT|MOBILE PAYMENT|AUTOPAY PAYMENT", "Card payment", "debt_payment"),
    (r"APPLECARD GSBANK|ACH Deposit Internet transfer", "Card payment", "debt_payment"),
    (r"DISCOVER.*PYMNT|Credit One Bank|Pmt to \*0117|Visa Platinum", "Card payment", "debt_payment"),
    (r"CAPITAL ONE|CHASE CARD|CITI ?CARD|BARCLAYCARD|SYNCHRONY|"
     r"CARDMEMBER SERV|BANK ?OF ?AMERICA.*(PMT|PAYMENT)",
     "Card payment", "debt_payment"),
    # ---- bills ----
    (r"PROG ADVANCED|PROGRESSIVE", "Insurance", "bill"),
    (r"AMERICAN STRATEG", "Insurance", "bill"),
    (r"Internet Transfer to \*0790|Regular Payment", "Car loan", "bill"),
    (r"CITY OF WENTZVILLE|MUNICIPAL ONLINE", "Utilities", "bill"),
    (r"AmerenMO|Speedpay", "Utilities", "bill"),
    (r"MERIDIAN WASTE", "Utilities", "bill"),
    (r"Spectrum", "Utilities", "bill"),
    (r"VZWRLSS|VERIZON", "Phone", "bill"),
    (r"ANTHROPIC|OPENAI|ELEVENLABS|Google Workspace|Patreon|CRUNCHYROLL|One Finance",
     "Subscriptions", "bill"),
    # ---- everyday spending ----
    (r"SCHNUCKS|SAMSCLUB|SAMS CLUB|WAL-MART|WM SUPERCENTER|TARGET|DOLLAR-GENERAL|"
     r"DOLLAR GENERAL|DOLLAR TREE|CHINA TOWN MARKET|KROGER",
     "Groceries & household", "spend"),
    (r"STARBUCKS|MCDONALD|TACO BELL|CHILIS|CHIPOTLE|KFC|DOMINOS|HARDEES|SUSHI|"
     r"88 CHINA|TUCANOS|APPLEBEE|BOBA|ANDY'S|7 BREW|DAIRY DELIGHT|PATISSERIE|"
     r"365 Market|CANTEEN|TST\*|BEST BITES|ARCH CAFE|ETHYL|GOAT HOUSE|LAVA LOUNGE|"
     r"PIZZA|BURGER|GRILL|CAFE|COFFEE|RESTAURANT|SENCINCY|TIN ROOF",
     "Restaurants & coffee", "spend"),
    (r"BLUESTONE|DIRT CHEAP|LLYWELYN|PUB|LIQUOR|BAR/NIGHTCLUB|4TH AND RACE",
     "Bars & liquor", "spend"),
    (r"ON THE RUN|\bQT\b|QUIKTRIP|CIRCLE ?K|BP#|FAS-TRIP|ORACLE PETROLEUM|SPEEDWAY|"
     r"PILOT_|FASTLANE|PEARCE CON|\bSHELL\b|\bEXXON\b|\bMOBIL\b",
     "Gas & convenience", "spend"),
    (r"AMAZON|AMZN|HOME DEPOT|BURLINGTON|WALGREENS|LOWES|BEST BUY|ETSY|EBAY|"
     r"STCHASPARKSPOOLS|DIRT|TEMU",
     "Amazon & shopping", "spend"),
    (r"KIND GOODS|MINT ST\. PETERS|MINT ST PETERS|DISPENSARY", "Dispensary", "spend"),
    (r"CASH APP|PAYPAL|APPLE CASH|VENMO|ZELLE|ATM Withdrawal|Foreign ATM|FIRST BANK",
     "Cash, ATM & P2P", "spend"),
    (r"UBER|LYFT|VIVID SEATS|AIRLINE|HOTEL|DELTA|SOUTHWEST|EVOLVE BY HUDSO|"
     r"HUDSONNEWS|RELAY|7-ELEVEN|GATEWAY ARCH|QUIKPARK|CIBT",
     "Travel & entertainment", "spend"),
    (r"MOTOR VEHICLE DEPT|DEPT OF STA", "Vehicle & registration", "spend"),
    (r"Amex Send", "Rent", "bill"),
    (r"MEINEKE|AUTO ZONE|O'REILLY|TIRE", "Car maintenance", "spend"),
    (r"Interest Charge|Late Fee|Annual Membership|Foreign ATM Transaction Fee",
     "Interest & fees", "fee"),
    (r"Alipay|KPAY|WeChat", "Travel & entertainment", "spend"),
    (r"POKEMON|GameStop|STEAM|Nintendo|PlayStation|Xbox", "Kids", "spend"),
    # ---- merchants seen in the older (Feb-May) statement format ----
    (r"PAPA JOHN|FREDDY'S|WHITE CASTLE|MCALISTER|GIOIA|CULVERS|STEAK-N-SHAKE|"
     r"NIJI ASIAN|JIMMY JOHN|PANERA|SUBWAY|WENDY|ARBY|SONIC|IMO'S|QDOBA|"
     r"RALLY'S|JACK IN THE BOX|WING|DONUT|BAKERY|CRUMBL|DUNKIN",
     "Restaurants & coffee", "spend"),
    (r"HY-VEE|ALDI|TRADER JOE|COSTCO|SAVE-A-LOT|FRESH THYME|DIERBERGS",
     "Groceries & household", "spend"),
    (r"KOHLS|POPSHELF|MARSHALLS|ROSS|TJ ?MAXX|OLD NAVY|WALMART\.COM|"
     r"SHEIN|WAYFAIR|IKEA|MENARDS|HOBBY LOBBY|MICHAELS|ACE HARDWARE",
     "Amazon & shopping", "spend"),
    (r"MOTOMART|CASEYS|REFUEL PANTRY|MURPHY|KUM ?& ?GO|LOVES|PHILLIPS 66|"
     r"CONOCO|SINCLAIR|MOBIL|VALERO",
     "Gas & convenience", "spend"),
    (r"MOCANNA|GREENLIGHT DISP|PROPER CANNABIS|SWADE|N'?BLISS",
     "Dispensary", "spend"),
    (r"COURTYARD BY|MARRIOTT|HILTON|HYATT|AIRBNB|VRBO|JETSTAR|SOUTHWEST AIR|"
     r"AMERICAN AIR|UNITED AIR|EXPEDIA|BOOKING\.COM|SSP\*|GDP\*",
     "Travel & entertainment", "spend"),
    (r"CAT\*|COLLECTOR|COUNTY TAX|PERSONAL PROPERTY", "Taxes & fees", "spend"),
    (r"WENTZVILLE PARKS|WENTZVILLE PK|PARKS ?& ?REC|YMCA|REC ?PLEX",
     "Kids", "spend"),
    (r"AFFIRM|KLARNA|AFTERPAY|SEZZLE", "Buy-now-pay-later", "debt_payment"),
]


def categorize(desc: str, rules) -> tuple[str, str]:
    d = desc.upper()
    for pat, cat, kind in rules:
        try:
            if re.search(pat, desc, re.I):
                return cat, kind
        except re.error:
            if pat.upper() in d:
                return cat, kind
    return "Uncategorized", "spend"


# ───────────────────────────────────────────────────────────────────────────
#  PDF / CSV PARSERS
# ───────────────────────────────────────────────────────────────────────────

MONEY = r"-?\$?([\d,]+\.\d\d)"


def _num(s):
    return float(str(s).replace("$", "").replace(",", "").strip())


def pdf_text(path) -> str:
    """Extract text from a PDF. Tries pdfplumber, then pypdf."""
    import warnings
    warnings.filterwarnings("ignore")
    if not os.path.exists(path):
        raise RuntimeError(f"There is no file at {path}")
    errs = []
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            t = "\n".join((p.extract_text() or "") for p in pdf.pages)
        if t.strip():
            return t
        errs.append("pdfplumber found no text")
    except ImportError:
        errs.append("pdfplumber not installed")
    except Exception as e:
        errs.append(f"pdfplumber: {e}")
    try:
        from pypdf import PdfReader
        t = "\n".join((pg.extract_text() or "") for pg in PdfReader(path).pages)
        if t.strip():
            return t
        errs.append("pypdf found no text")
    except ImportError:
        errs.append("pypdf not installed")
    except Exception as e:
        errs.append(f"pypdf: {e}")
    if all("not installed" in e for e in errs):
        raise RuntimeError(
            "No PDF reader is installed.\n\n"
            "Run SETUP.bat in this folder, or from a command prompt:\n"
            "    python -m pip install pdfplumber pypdf\n\n"
            "(CSV import works without either of these.)")
    return ""


def _y2(y):
    y = int(y)
    return 2000 + y if y < 80 else 1900 + y


def _dedupe_caps(line):
    """These PDFs draw bold text twice ("SSTTAATTEEMMEENNTT"). Collapse it."""
    return re.sub(r"([A-Za-z])\1", r"\1", line)


def detect_type(text: str, filename: str = "") -> str:
    """
    Score each format on markers that only appear in a statement's own
    letterhead. A checking statement lists card payments as MERCHANTS
    ("Credit One Bank,N.A. - Payment"), so a bare brand name is not enough
    to identify the issuer — that mistake mislabels a whole bank statement.
    """
    t = text[:20000]
    tl = t.lower()
    flat = _dedupe_caps(t).lower()
    fn = (filename or "").lower()

    score = {"fccu": 0, "creditone": 0, "amex": 0, "apple": 0}

    # First Community CU
    for mark, pts in (("firstcommunity.com", 4), ("member number:", 3),
                      ("express24", 3), ("chesterfield airport road", 3),
                      ("share savings account", 2), ("checking i account", 2),
                      ("summary of accounts", 2)):
        if mark in tl or mark in flat:
            score["fccu"] += pts

    # Credit One — needs its own letterhead, not a merchant mention
    for mark, pts in (("credit one bank credit card statement", 6),
                      ("creditonebank.com", 4), ("p.o. box 98873", 3),
                      ("las vegas, nv 89193", 3)):
        if mark in tl or mark in flat:
            score["creditone"] += pts

    # American Express
    for mark, pts in (("american express", 3), ("americanexpress.com", 4),
                      ("membership rewards", 3), ("pay over time", 2)):
        if mark in tl:
            score["amex"] += pts

    # Apple Card
    for mark, pts in (("apple card", 4), ("goldman sachs bank usa", 4),
                      ("daily cash", 3), ("card.apple.com", 3)):
        if mark in tl:
            score["apple"] += pts

    for key, pat in (("fccu", "fccu|first community|checking|debit"),
                     ("creditone", "credit ?one"), ("amex", "amex|american express"),
                     ("apple", "apple")):
        if re.search(pat, fn):
            score[key] += 1

    best = max(score, key=lambda k: score[k])
    return best if score[best] >= 3 else "unknown"



_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _mdy(mon, day, year):
    mi = _MONTHS.get(str(mon)[:3].lower())
    return f"{int(year):04d}-{mi:02d}-{int(day):02d}" if mi else ""


def parse_amex(text, source="") -> tuple[list[Txn], dict]:
    txns, meta = [], {}
    m = re.search(r"New Balance\s+\$?([\d,]+\.\d\d)", text)
    if m:
        meta["balance"] = _num(m.group(1))
    m = re.search(r"Minimum Payment Due\s+\$?([\d,]+\.\d\d)", text)
    if m:
        meta["minimum"] = _num(m.group(1))
    m = re.search(r"Payment Due Date\s+(\d\d)/(\d\d)/(\d\d)", text)
    if m:
        meta["due"] = f"{_y2(m.group(3))}-{m.group(1)}-{m.group(2)}"
    m = re.search(r"Pay Over Time\s+\d\d/\d\d/\d{4}\s+([\d.]+)%", text)
    if m:
        meta["apr"] = float(m.group(1)) / 100
    m = re.search(r"Closing Date\s+(\d\d)/(\d\d)/(\d\d)\b", text)
    if m:
        meta["statement_date"] = f"{_y2(m.group(3))}-{m.group(1)}-{m.group(2)}"
    m = re.search(r"Account Ending\s+([\d-]+)", text)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 4:
            meta["last4"] = digits[-4:]
    meta["account"] = "AMEX Gold"

    for line in text.split("\n"):
        m = re.match(r"\s*(\d\d)/(\d\d)/(\d\d)(\*?)\s+(.*?)\s+(-?\$[\d,]+\.\d\d)\s*$", line)
        if not m:
            continue
        mm, dd, yy, star, desc, amt = m.groups()
        a = _num(amt)
        if amt.strip().startswith("-"):
            a = -abs(a)
        # strip the trailing "Pay Over Time / Cash Advance / foreign amount" columns
        desc = re.sub(r"\s+Pay Over Time\b.*$", "", desc)
        desc = re.sub(r"\s+(and/or Cash|Advance)\s*$", "", desc).strip()
        if not desc:
            continue
        t = Txn(f"{_y2(yy)}-{mm}-{dd}", desc, a, "AMEX Gold", source=source)
        if re.match(r"interest charge|total interest|late fee|annual membership",
                    desc, re.I):
            t.kind, t.category = "fee", "Interest & fees"
        txns.append(t)
    return txns, meta


def parse_apple(text, source="") -> tuple[list[Txn], dict]:
    txns, meta = [], {"account": "Apple Card"}
    lines = [l.strip() for l in text.split("\n")]

    # "Previous Total Balance" also contains "Total Balance", so anchor to the
    # start of the line or the previous month's figure wins.
    for i, l in enumerate(lines):
        m = re.match(r"^Total Balance\s+\$?([\d,]+\.\d\d)\s*$", l)
        if m:
            meta["balance"] = _num(m.group(1))
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            d = re.match(r"^as of\s+(\w{3})\w*\s+(\d{1,2}),\s*(\d{4})", nxt)
            if d:
                meta["statement_date"] = _mdy(*d.groups())
            break
    if "statement_date" not in meta:
        m = re.search(r"Your \w+ Balance[\s\S]{0,40}?as of\s+(\w{3})\w*\s+"
                      r"(\d{1,2}),\s*(\d{4})", text)
        if m:
            meta["statement_date"] = _mdy(*m.groups())
    # the summary row collapses to: "$540.43 $25.00 Aug 31, 2026"
    for i, l in enumerate(lines):
        if re.search(r"Your \w+ Balance\b.*Minimum", l):
            for j in range(i, min(i + 4, len(lines))):
                m = re.match(r"^\$([\d,]+\.\d\d)\s+\$([\d,]+\.\d\d)\s+\w{3}", lines[j])
                if m:
                    meta.setdefault("balance", _num(m.group(1)))
                    meta["minimum"] = _num(m.group(2))
                    break
            break
    m = re.search(r"Annual Percentage Rate \(APR\)\s*([\d.]+)\s*%", text)
    if m:
        meta["apr"] = float(m.group(1)) / 100

    for line in text.split("\n"):
        m = re.match(r"\s*(\d\d)/(\d\d)/(\d{4})\s+(.*?)\s+(-?\$[\d,]+\.\d\d)\s*$", line)
        if not m:
            continue
        mm, dd, yy, desc, amt = m.groups()
        a = _num(amt)
        if amt.strip().startswith("-"):
            a = -abs(a)
        desc = re.sub(r"\s+\d+%\s+\$[\d,.]+\s*$", "", desc).strip()
        txns.append(Txn(f"{yy}-{mm}-{dd}", desc, a, "Apple Card", source=source))
    return txns, meta


def parse_creditone(text, source="") -> tuple[list[Txn], dict]:
    txns, meta = [], {"account": "Credit One"}
    m = re.search(r"Account Number\s*([\d ]{8,})", text)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 4:
            meta["last4"] = digits[-4:]
    m = re.search(r"New Balance\s+\$?([\d,]+\.\d\d)", text)
    if m:
        meta["balance"] = _num(m.group(1))
    m = re.search(r"Minimum Payment Due\s+\$?([\d,]+\.\d\d)", text)
    if m:
        meta["minimum"] = _num(m.group(1))
    m = re.search(r"Credit Limit\s+\$?([\d,]+\.\d\d)", text)
    if m:
        meta["limit"] = _num(m.group(1))
    m = re.search(r"Purchases\s+([\d.]+)\s*%", text)
    if m:
        meta["apr"] = float(m.group(1)) / 100
    year = 2026
    m = re.search(r"to\s+(\w{3})\w*\s+(\d{1,2}),\s*(\d{4})", text)
    if m:
        year = int(m.group(3))
        meta["statement_date"] = _mdy(*m.groups())
    m = re.search(r"Statement Closing Date\s+(\d\d)/(\d\d)/(\d\d)\b", text)
    if m:
        meta["statement_date"] = f"{_y2(m.group(3))}-{m.group(1)}-{m.group(2)}"
    # only read lines inside the TRANSACTIONS block
    body = text
    i = text.upper().find("TRANSACTIONS")
    if i >= 0:
        body = text[i:]
    j = body.upper().find("INTEREST CHARGE CALCULATION")
    if j > 0:
        body = body[:j]
    for line in body.split("\n"):
        mm = re.match(r"\s*(?:\S+\s+)?(\d\d)/(\d\d)\s+(\d\d)/(\d\d)\s+"
                      r"(.*?)\s+(-?[\d,]+\.\d\d)\s*$", line)
        if not mm:
            continue
        mo, dd, _, _, desc, amt = mm.groups()
        d = desc.strip()
        if not d or re.search(r"^total\b", d, re.I):
            continue
        txns.append(Txn(f"{year}-{mo}-{dd}", d, _num(amt), "Credit One", source=source))
    return txns, meta


def _is_bold_header(line):
    """Section headers are drawn bold, so their digits come out doubled
    ("117755992200229900779900"). The summary table at the top is not bold,
    so this cleanly separates a real section header from a summary row."""
    digits = re.sub(r"\D", "", line)
    return len(digits) >= 8 and digits[0::2] == digits[1::2]


def parse_fccu(text, source="") -> tuple[list[Txn], dict]:
    """
    First Community CU bundles savings, checking and loans into one PDF, and
    the layout changed partway through 2026:

        older:  MM/DD  description  -123.45  1,248.54     (one date, signed)
        newer:  MM/DD MM/DD  description  123.45  2,618.11 (two dates, unsigned)

    Both are handled. Only the checking section is returned — a loan's
    "Regular Payment" is the far side of a transfer already counted in
    checking, so including it would double the car payment.
    """
    txns, meta = [], {"account": "Checking"}
    year = 2026
    m = re.search(r"STATEMENT ENDING\s+(\d\d)/(\d\d)/(\d{4})", _dedupe_caps(text))
    if m:
        year = int(m.group(3))
    section = None
    for raw in text.split("\n"):
        line = raw.rstrip()
        flat = _dedupe_caps(line).upper()
        if _is_bold_header(line):
            if re.search(r"\bCHECKING\b", flat):
                section = "checking"
            elif re.search(r"\bSAVINGS\b|\bSHARE\b", flat):
                section = "savings"
            else:
                section = "loan"
            continue
        if section != "checking":
            continue

        two = re.match(r"\s*(\d\d)/(\d\d)\s+(\d\d)/(\d\d)\s+(.*?)\s+"
                       r"([\d,]+\.\d\d)\s+([\d,]+\.\d\d)\s*$", line)
        one = None if two else re.match(
            r"\s*(\d\d)/(\d\d)\s+(.*?)\s+(-?[\d,]+\.\d\d)\s+([\d,]+\.\d\d)\s*$",
            line)
        if two:
            mo, dd, _, _, desc, amt, _bal = two.groups()
            signed = None
        elif one:
            mo, dd, desc, amt, _bal = one.groups()
            signed = amt.strip().startswith("-")
        else:
            continue

        d = re.sub(r"\s{2,}", " ", desc).strip(" _—-")
        if not d or re.search(r"Beginning Balance|Ending Balance", d, re.I):
            continue
        val = abs(_num(amt))   # sign is carried by `signed`, not the digits
        if signed is None:
            inflow = bool(re.search(r"\bDeposit\b|\bCredit\b|Refund", d, re.I))
        else:
            inflow = not signed
        txns.append(Txn(f"{year}-{mo}-{dd}", d, (-1 if inflow else 1) * val,
                        "Checking", source=source))
    return txns, meta


def parse_csv(path, account_hint="Checking") -> tuple[list[Txn], dict]:
    """
    Generic CSV importer. Finds date / description / amount columns by name.
    Handles both single 'Amount' columns and split Debit/Credit columns.
    """
    txns = []
    skipped = 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rdr = csv.DictReader(f, dialect=dialect)
        cols = {(c or "").strip().lower(): c for c in (rdr.fieldnames or [])}

        def find(*names):
            for n in names:
                for lc, orig in cols.items():
                    if n in lc:
                        return orig
            return None

        c_date = find("date", "posted", "transaction date")
        c_desc = find("description", "payee", "memo", "name", "detail")
        c_amt = find("amount", "value")
        c_deb = find("debit", "withdrawal")
        c_cre = find("credit", "deposit")
        if not c_date or not c_desc:
            raise RuntimeError(
                "Could not find date/description columns in that CSV.\n"
                f"Columns seen: {', '.join(rdr.fieldnames or [])}")
        for row in rdr:
            ds = (row.get(c_date) or "").strip()
            if not ds:
                continue
            iso = None
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y",
                        "%m-%d-%Y", "%b %d, %Y", "%d-%b-%Y", "%Y/%m/%d"):
                try:
                    iso = datetime.strptime(ds, fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            if not iso:
                skipped += 1
                continue
            amt = 0.0
            if c_amt and (row.get(c_amt) or "").strip():
                raw = row[c_amt].replace("$", "").replace(",", "").strip()
                neg = raw.startswith("(") or raw.startswith("-")
                raw = raw.strip("()-+ ")
                try:
                    amt = float(raw)
                except ValueError:
                    skipped += 1
                    continue
                # Most banks export spending as negative. Flip to "out = positive".
                amt = amt if neg else -amt
            else:
                dv = (row.get(c_deb) or "").replace("$", "").replace(",", "").strip()
                cv = (row.get(c_cre) or "").replace("$", "").replace(",", "").strip()
                try:
                    amt = float(dv) if dv else -float(cv) if cv else 0.0
                except ValueError:
                    skipped += 1
                    continue
            if amt == 0:
                continue
            txns.append(Txn(iso, (row.get(c_desc) or "").strip(), round(amt, 2),
                            account_hint, source=os.path.basename(path)))
    return txns, {"account": account_hint, "skipped": skipped}


def import_file(path, account_hint=None) -> tuple[list[Txn], dict, str]:
    """Returns (txns, meta, message)."""
    name = os.path.basename(path)
    if path.lower().endswith((".csv", ".txt", ".tsv")):
        t, meta = parse_csv(path, account_hint or "Checking")
        sk = meta.get("skipped", 0)
        msg = f"Imported {len(t)} transactions from {name}"
        if sk:
            msg += (f"\n\n{sk} row(s) were skipped — the date or the amount "
                    "couldn't be read. Open the file and check those rows; "
                    "everything else came through.")
        if not t:
            raise RuntimeError(
                f"{name} has date and description columns, but no row had a "
                "readable date AND amount.\n\nCheck that the amount column "
                "holds numbers, and that dates look like 07/05/2026 or "
                "2026-07-05.")
        return t, meta, msg

    text = pdf_text(path)
    if len(text.strip()) < 200:
        raise RuntimeError(
            f"{name} has no readable text — it's a scanned image, not a text PDF.\n\n"
            "Easiest fix: log in to First Community online banking, go to your "
            "checking account history, and download the transactions as CSV. "
            "Then import that file here instead.\n\n"
            "(Card statements from AMEX, Apple and Credit One import directly as PDFs.)")

    kind = detect_type(text, name)
    if kind == "amex":
        t, meta = parse_amex(text, name)
    elif kind == "apple":
        t, meta = parse_apple(text, name)
    elif kind == "creditone":
        t, meta = parse_creditone(text, name)
    elif kind == "fccu":
        t, meta = parse_fccu(text, name)
    else:
        raise RuntimeError(f"Didn't recognize {name} as a statement I know how to read.")
    return t, meta, f"Imported {len(t)} transactions from {name} ({kind.upper()})"


STATEMENT_EXTS = (".pdf", ".csv", ".tsv", ".txt")


def find_statements(root, skip_dirs=()):
    """
    Every statement-looking file under `root`, including subfolders.
    Skips the app's own folder and anything hidden, so exported schedules and
    the data file never get mistaken for a statement.
    """
    skip = {os.path.abspath(d) for d in skip_dirs}
    out = []
    for dirpath, dirnames, files in os.walk(root):
        ap = os.path.abspath(dirpath)
        if ap in skip or any(ap.startswith(x + os.sep) for x in skip):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if not d.startswith((".", "__")) and d.lower() != "_to_delete"]
        for f in sorted(files):
            if f.startswith((".", "~$")):
                continue
            if f.lower().endswith(STATEMENT_EXTS):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def file_stamp(path):
    """Identity of a file as it is right now — changes if the file changes."""
    try:
        st = os.stat(path)
        return f"{int(st.st_size)}:{int(st.st_mtime)}"
    except OSError:
        return ""


# ───────────────────────────────────────────────────────────────────────────
#  STORE
# ───────────────────────────────────────────────────────────────────────────


class Store:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self.txns: list[Txn] = []
        self.debts: list[Debt] = []
        self.bills: list[Bill] = []
        self.user_rules = []
        self.user_rules = []
        self.rules = [list(r) for r in DEFAULT_RULES]
        self.categories = list(DEFAULT_CATEGORIES)
        self.cuts: dict[str, float] = {}
        self.settings = {
            "paycheck": 1982.85, "checks_per_month": 2, "rent": 900.0,
            "rent_on_card": True, "extra_income": 0.0,
            "income_bumps": [],       # [{"from_month":4,"amount":210,"note":"401k loan ends"}]
            "windfalls": [],          # [{"month":1,"amount":1000,"note":"reimbursement"}]
            "start": date.today().replace(day=1).isoformat(),
            "baseline_months": [],   # empty = use every month that has data
            "rules_version": RULES_VERSION,
            "notes": [],             # your own reminders, shown on the Dashboard
            "auto_start": True,      # roll the plan forward to the current month
            "scan_root": "",         # folder to sweep for statements ("" = the
                                     # folder this app sits in, and its parent)
            "seen_files": {},        # path -> size:mtime of what's been imported
            # gross (pre-tax) pay is what mortgage lenders use, not take-home
            "gross_monthly": 0.0,    # 0 = estimate it from take-home
            "home": {                # Home tab inputs
                "price": 0.0,        # 0 / price_auto = use the max you qualify for
                "price_auto": True,
                "down": 15000.0, "apr": 0.0667, "years": 30,
                "tax_rate": 0.0131, "insurance": 3940.0, "pmi_rate": 0.0075,
                "hoa": 0.0, "front": 0.28, "back": 0.36,
            },
        }
        self.goals: list[Goal] = []
        self.accounts: list[Account] = []

    # ---- persistence ----
    def to_dict(self):
        return {
            "txns": [asdict(t) for t in self.txns],
            "debts": [asdict(d) for d in self.debts],
            "bills": [asdict(b) for b in self.bills],
            "goals": [asdict(g) for g in self.goals],
            "accounts": [asdict(a) for a in self.accounts],
            "user_rules": getattr(self, "user_rules", []),
            "categories": self.categories,
            "cuts": self.cuts,
            "settings": self.settings,
        }

    def save(self, path=None):
        """
        Write safely. The data file is rewritten on every slider release, so an
        interrupted write must never be able to destroy it: build a temp file,
        fsync it, keep the previous copy as .bak, then swap it in atomically.
        """
        self.settings["rules_version"] = RULES_VERSION
        p = path or self.path
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        if os.path.exists(p):
            bak = p + ".bak"
            try:
                if os.path.exists(bak):
                    os.remove(bak)
                os.replace(p, bak)
            except OSError:
                pass
        os.replace(tmp, p)
        return p

    def load_safe(self, path=None):
        """
        Load, falling back to the backup if the main file is unreadable.
        Returns (ok, message). Never raises — a corrupt file must not stop the
        app from opening.
        """
        p = path or self.path
        # A fresh clone has no data file. Start from the sample so the app opens
        # with something to look at rather than an empty shell.
        if not os.path.exists(p):
            sample = os.path.join(os.path.dirname(p) or ".",
                                  "finplan_data.sample.json")
            if os.path.exists(sample):
                try:
                    ok = self.load(sample)
                    self.save(p)
                    return (ok,
                            "Started from the bundled sample data so you have "
                            "something to explore. Nothing in it is real — import "
                            "your own statements, or edit the figures on the Debts, "
                            "Bills and Accounts tabs, to make it yours.")
                except Exception:
                    pass
        try:
            return (self.load(p), "") if os.path.exists(p) else (False, "")
        except Exception as e:
            bak = p + ".bak"
            if os.path.exists(bak):
                try:
                    ok = self.load(bak)
                    broken = p + ".broken"
                    try:
                        if os.path.exists(broken):
                            os.remove(broken)
                        os.replace(p, broken)
                    except OSError:
                        pass
                    self.save(p)
                    return (ok,
                            f"Your data file was damaged ({type(e).__name__}) so the "
                            f"backup was used instead. The damaged copy is saved as "
                            f"{os.path.basename(broken)} if you want it.")
                except Exception:
                    pass
            return (False,
                    f"Your data file couldn't be read ({type(e).__name__}) and there "
                    "is no usable backup, so the app has started empty. The file has "
                    "NOT been overwritten — move it somewhere safe before saving.")

    def load(self, path=None):
        p = path or self.path
        if not os.path.exists(p):
            return False
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        self.txns = [Txn(**t) for t in d.get("txns", [])]
        self.debts = [Debt(**x) for x in d.get("debts", [])]
        self.bills = [Bill(**x) for x in d.get("bills", [])]
        self.goals = [Goal(**x) for x in d.get("goals", [])]
        self.accounts = [Account(**x) for x in d.get("accounts", [])]
        # The built-in rules belong to the app: if this file was written by an
        # older version, take the current ones so bug fixes actually land.
        # Anything you added yourself is kept and always wins (checked first).
        # Only rules under "user_rules" are yours. The old "rules" key was always
        # a copy of the app's built-ins, so it is dropped on load — otherwise a
        # rule the app later FIXED would live on in your file and keep winning.
        self.user_rules = [list(r) for r in d.get("user_rules", [])]
        self.rules = self.user_rules + [list(r) for r in DEFAULT_RULES]
        self.categories = d.get("categories", self.categories)
        self.cuts = d.get("cuts", {})
        self.settings.update(d.get("settings", {}))
        return True

    # ---- transactions ----
    def add_txns(self, txns) -> tuple[int, int]:
        """Adds new transactions and folds any NEW month into the averaging
        window. Without this, importing more statements changes nothing you
        can see — the baseline stays pinned to the months you already had."""
        known_months = set(self.months())
        have = {t.key() for t in self.txns}
        added = dup = 0
        # Two genuinely separate identical charges on one day are NOT duplicates.
        # Number them within the batch so the second one survives, while
        # re-importing the same statement still dedupes cleanly.
        seen_in_batch = {}
        for t in txns:
            base = (f"{t.date}|{t.account}|{round(t.amount,2)}"
                    f"|{t.desc[:40].upper()}")
            t.seq = seen_in_batch.get(base, 0)
            seen_in_batch[base] = t.seq + 1
        for t in txns:
            if t.key() in have:
                dup += 1
                continue
            c, k = categorize(t.desc, self.rules)
            if k == "spend" and c == "Uncategorized" and t.amount < 0:
                c, k = "Deposit & refunds", "income"
            t.category, t.kind = c, k
            have.add(t.key())
            self.txns.append(t)
            added += 1
        self.txns.sort(key=lambda t: t.date)
        sel = self.settings.get("baseline_months") or []
        if sel:
            fresh = [m for m in self.months() if m not in known_months]
            if fresh:
                self.settings["baseline_months"] = sorted(set(sel) | set(fresh))
        return added, dup

    def match_debt(self, account_name, last4=""):
        """
        Work out which account a statement belongs to.

        The last 4 of the account number wins, because you can hold two cards
        from the SAME issuer — a Credit One carrying a balance and another
        Credit One you only use for Amazon. Falling back to the issuer name
        alone would quietly update the wrong one, so when the issuer matches
        more than one account and the statement gives no number to go on,
        this returns "ambiguous" rather than guessing.

        Returns a Debt, None, or the string "ambiguous".
        """
        last4 = re.sub(r"\D", "", str(last4 or ""))[-4:]
        if last4:
            hit = [d for d in self.debts if d.last4 and d.last4[-4:] == last4]
            if len(hit) == 1:
                return hit[0]
            if len(hit) > 1:
                return "ambiguous"
        if not account_name:
            return None
        a = account_name.lower()
        keys = [("american express", "amex"), ("amex", "amex"), ("apple", "apple"),
                ("credit one", "credit one"), ("discover", "discover"),
                ("capital one", "capital one"), ("visa", "visa")]
        want = next((v for k, v in keys if k in a), a)
        cands = [d for d in self.debts if want in d.name.lower()]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            # more than one card from this issuer, and nothing to tell them apart
            if last4:
                return "unknown_account"
            return "ambiguous"
        return None

    def apply_statement(self, meta):
        """
        Update a card's balance from a statement — but ONLY if that statement
        is newer than whatever the balance is currently true as of. A number
        you typed in yourself counts as today's, so it stands until a genuinely
        newer statement arrives. Returns a description of what changed, or None.
        """
        if "balance" not in meta:
            return None
        d = self.match_debt(meta.get("account"), meta.get("last4", ""))
        if d == "ambiguous":
            return {"debt": meta.get("account", "?"), "skipped": True,
                    "ambiguous": True, "asof": "", "statement": "",
                    "old": 0.0, "new": meta["balance"],
                    "why": ("more than one account from this issuer and the "
                            "statement gives no account number — set the last 4 "
                            "on each of them under Debts")}
        if d == "unknown_account":
            return {"debt": f"{meta.get('account','?')} ending {meta.get('last4')}",
                    "skipped": True, "ambiguous": True, "asof": "", "statement": "",
                    "old": 0.0, "new": meta["balance"],
                    "why": (f"this statement is for the card ending "
                            f"{meta.get('last4')}, which isn't set on any account "
                            "yet — add it, or fill in the last 4 under Debts")}
        if d is None:
            return None
        sdate = meta.get("statement_date") or ""
        if not sdate:
            return None
        cur = d.balance_asof or ""
        if cur and sdate <= cur:
            return {"debt": d.name, "skipped": True, "asof": cur,
                    "statement": sdate, "old": d.balance, "new": meta["balance"]}
        old = d.balance
        d.balance = float(meta["balance"])
        d.balance_asof = sdate
        d.balance_source = "statement"
        if "minimum" in meta:
            d.minimum = float(meta["minimum"])
        if "apr" in meta and meta["apr"]:
            d.apr = float(meta["apr"])
        if "limit" in meta and meta["limit"]:
            d.limit = float(meta["limit"])
        if meta.get("last4") and not d.last4:
            d.last4 = meta["last4"]
        return {"debt": d.name, "skipped": False, "asof": sdate,
                "statement": sdate, "old": old, "new": d.balance}

    def file_already_imported(self, path):
        """True only if this exact file, unchanged, has been imported before."""
        seen = self.settings.setdefault("seen_files", {})
        key = os.path.abspath(path)
        stamp = file_stamp(path)
        return bool(stamp) and seen.get(key) == stamp

    def prune_seen_files(self):
        seen = self.settings.setdefault("seen_files", {})
        gone = [k for k in seen if not os.path.exists(k)]
        for k in gone:
            del seen[k]
        return len(gone)

    def mark_file_imported(self, path):
        self.settings.setdefault("seen_files", {})[os.path.abspath(path)] = \
            file_stamp(path)

    def new_months_since(self, before):
        return [m for m in self.months() if m not in set(before)]

    def recategorize(self, keep_manual=True):
        for t in self.txns:
            if keep_manual and t.manual:
                continue
            c, k = categorize(t.desc, self.rules)
            # money coming IN that no rule claimed is a refund/credit, not spending
            if k == "spend" and c == "Uncategorized" and t.amount < 0:
                c, k = "Deposit & refunds", "income"
            t.category, t.kind = c, k

    def one_time_candidates(self, threshold=400.0):
        """Big one-off spends worth reviewing — they distort the monthly baseline."""
        return sorted([t for t in self.txns if t.kind == "spend" and t.amount >= threshold],
                      key=lambda t: -t.amount)

    def mark_trip(self, start, end, label):
        """Exclude every spend between two dates — a holiday isn't a monthly habit."""
        n = 0
        for t in self.txns:
            if start <= t.date <= end and t.kind == "spend":
                t.exclude, t.note = True, label
                n += 1
        return n

    def trips(self):
        """Distinct trip labels currently applied, with their totals."""
        out = {}
        for t in self.txns:
            if t.exclude and t.note:
                a, b = out.get(t.note, [0.0, 0])
                out[t.note] = [a + t.amount, b + 1]
        return out

    def excluded_total(self, months=None):
        ms = months or self.baseline_months()
        return sum(t.amount for t in self.txns
                   if t.exclude and t.kind == "spend" and t.date[:7] in ms)

    def roll_start_forward(self):
        """
        Move the plan's month 1 to the current month, and carry dated events
        with it. Without this the app keeps planning from a month that has
        already passed, and a windfall pinned to "month 1" never arrives.
        Returns a note if anything moved.
        """
        if not self.settings.get("auto_start", True):
            return ""
        today = date.today().replace(day=1)
        old = self.settings.get("start") or today.isoformat()
        try:
            oy, om, _ = (int(x) for x in old.split("-"))
        except ValueError:
            self.settings["start"] = today.isoformat()
            return ""
        shift = (today.year - oy) * 12 + (today.month - om)
        if shift <= 0:
            return ""
        self.settings["start"] = today.isoformat()
        dropped = []
        kept = []
        for w in self.settings.get("windfalls", []):
            n = int(w.get("month", 1)) - shift
            if n >= 1:
                w["month"] = n
                kept.append(w)
            else:
                dropped.append(w.get("note") or f"${w.get('amount',0):,.0f}")
        self.settings["windfalls"] = kept
        for b in self.settings.get("income_bumps", []):
            b["from_month"] = max(1, int(b.get("from_month", 1)) - shift)
        msg = (f"The plan has moved forward {shift} month(s) to "
               f"{today:%B %Y}.")
        if dropped:
            msg += ("  These one-off amounts are now in the past and have been "
                    "removed: " + ", ".join(dropped) +
                    ".  If one hasn't actually arrived, add it again on the "
                    "Debts tab.")
        return msg

    def months(self):
        return sorted({t.date[:7] for t in self.txns})

    def monthly_spend_series(self, months=None):
        """Actual spend per calendar month — for the trend chart."""
        ms = months or self.months()
        out = {m: 0.0 for m in ms}
        for t in self.txns:
            if t.kind == "spend" and not t.exclude and t.date[:7] in out:
                out[t.date[:7]] += t.amount
        return out

    def category_series(self, category, months=None):
        ms = months or self.months()
        out = {m: 0.0 for m in ms}
        for t in self.txns:
            if (t.kind == "spend" and not t.exclude and t.category == category
                    and t.date[:7] in out):
                out[t.date[:7]] += t.amount
        return out

    def actual_debt_payments(self, months=None):
        """What you really sent to cards each month, from the statements."""
        ms = months or self.months()
        out = {m: 0.0 for m in ms}
        for t in self.txns:
            if t.kind == "debt_payment" and t.date[:7] in out:
                out[t.date[:7]] += abs(t.amount)
        return out

    def baseline_months(self):
        """Months that count toward the monthly averages."""
        want = self.settings.get("baseline_months") or []
        have = self.months()
        sel = [m for m in want if m in have]
        return sel or have

    def spend_by_category(self, months=None):
        """Average monthly spend per category over the given months."""
        ms = months or self.baseline_months()
        if not ms:
            return {}
        out = {}
        for t in self.txns:
            if t.kind != "spend" or t.exclude or t.date[:7] not in ms:
                continue
            out[t.category] = out.get(t.category, 0.0) + t.amount
        n = max(len(ms), 1)
        return {k: v / n for k, v in sorted(out.items(), key=lambda x: -x[1])}

    def merchants_in(self, category, months=None):
        ms = months or self.baseline_months()
        out = {}
        for t in self.txns:
            if (t.kind != "spend" or t.exclude or t.category != category
                    or t.date[:7] not in ms):
                continue
            k = clean_merchant(t.desc)
            out[k] = out.get(k, 0.0) + t.amount
        n = max(len(ms), 1)
        return {k: v / n for k, v in sorted(out.items(), key=lambda x: -x[1])}

    def bills_total(self):
        return sum(b.amount for b in self.bills if b.active)

    def spend_total(self, months=None):
        return sum(self.spend_by_category(months).values())

    def cuts_total(self):
        """Net monthly change. Positive = trimming, negative = spending more."""
        return sum(self.cuts.values())

    def planned_spend(self, months=None):
        return max(0.0, self.spend_total(months) - self.cuts_total())

    # ---- cash flow ----
    def income_base(self):
        s = self.settings
        return s["paycheck"] * s["checks_per_month"] + s.get("extra_income", 0.0)

    def gross_monthly(self):
        """Pre-tax monthly income. Lenders qualify you on this, not take-home."""
        g = self.settings.get("gross_monthly", 0) or 0
        if g > 0:
            return float(g)
        return round(self.income_base() / 0.75, 2)   # rough estimate

    def gross_is_estimated(self):
        return not (self.settings.get("gross_monthly", 0) or 0) > 0

    def debt_payments_monthly(self):
        """What a lender counts against you: card minimums + the car loan."""
        mins = sum(d.minimum for d in self.debts if d.include and d.balance > 0)
        car = sum(b.amount for b in self.bills
                  if b.active and re.search(r"car loan|auto loan", b.name, re.I))
        return mins + car

    def goals_monthly(self):
        return sum(g.monthly for g in self.goals)

    # ---- spending accounts -------------------------------------------------

    def category_budget(self, cat, months=None):
        """What you plan to spend in a category — the baseline less any trim."""
        base = self.spend_by_category(months).get(cat, 0.0)
        return max(0.0, base - self.cuts.get(cat, 0.0))

    def bill_amount(self, name):
        for b in self.bills:
            if b.name == name and b.active:
                return b.amount
        return 0.0

    def bill_obj(self, name):
        for b in self.bills:
            if b.name == name:
                return b
        return None

    def account_monthly(self, acc, months=None):
        cats = sum(self.category_budget(c, months) for c in acc.categories)
        bills = sum(self.bill_amount(n) for n in acc.bills)
        return cats + bills

    def account_breakdown(self, acc, months=None):
        """
        What comes out of each paycheck for this account.

        A bill pinned to a specific check is taken off the top of that check in
        full — a $420 car payment due on the 5th has to come out of the 1st
        check, not half from each. The split slider then only divides what's
        left over, which is the part you actually have discretion about.
        """
        pin1 = pin2 = 0.0
        pinned = []
        for n in acc.bills:
            b = self.bill_obj(n)
            if not b or not b.active:
                continue
            if b.check == 1:
                pin1 += b.amount
                pinned.append((n, b.amount, 1))
            elif b.check == 2:
                pin2 += b.amount
                pinned.append((n, b.amount, 2))
        flexible = (sum(self.category_budget(c, months) for c in acc.categories)
                    + sum(self.bill_amount(n) for n in acc.bills
                          if (self.bill_obj(n) or Bill("", 0)).check == 0))
        p = min(max(float(acc.split), 0.0), 100.0) / 100.0
        first = pin1 + flexible * p
        second = pin2 + flexible * (1 - p)
        total = first + second
        return {"total": total, "first": first, "second": second,
                "flexible": flexible, "pinned_first": pin1, "pinned_second": pin2,
                "pinned": pinned,
                "pct_first": p * 100, "pct_second": (1 - p) * 100,
                "per_check_even": total / 2}

    def card_payments(self, months=None):
        """
        What the plan says to send each card this month, and which check it
        comes from. Uses month 1 of the projection so the figures match the Plan
        tab exactly.
        """
        r = project(self)
        first_row = r["rows"][0]["paid"] if r["rows"] else {}
        out = []
        for d in self.debts:
            if not d.include or d.balance <= 0:
                continue
            amt = first_row.get(d.name, d.minimum)
            out.append({"name": d.name, "amount": amt, "check": int(d.check or 0),
                        "apr": d.apr, "minimum": d.minimum})
        return out

    def assigned_categories(self):
        out = {}
        for a in self.accounts:
            for c in a.categories:
                out[c] = a.name
        return out

    def assigned_bills(self):
        out = {}
        for a in self.accounts:
            for n in a.bills:
                out[n] = a.name
        return out

    def unassigned_categories(self, months=None):
        taken = set(self.assigned_categories())
        return {c: v for c, v in self.spend_by_category(months).items()
                if c not in taken and v > 0}

    def unassigned_bills(self):
        taken = set(self.assigned_bills())
        return {b.name: b.amount for b in self.bills
                if b.active and b.name not in taken}

    def assign_category(self, cat, account_name):
        """Move a category to one account (or nowhere if account_name is '')."""
        for a in self.accounts:
            if cat in a.categories:
                a.categories.remove(cat)
        for a in self.accounts:
            if a.name == account_name:
                a.categories.append(cat)
                break

    def assign_bill(self, name, account_name):
        for a in self.accounts:
            if name in a.bills:
                a.bills.remove(name)
        for a in self.accounts:
            if a.name == account_name:
                a.bills.append(name)
                break

    def paycheck_plan(self, months=None, include_cards=True):
        """Everything leaving each paycheck: accounts, then card payments."""
        rows = []
        for a in self.accounts:
            b = self.account_breakdown(a, months)
            if b["total"] > 0 or a.categories or a.bills:
                rows.append({"account": a, **b})
        cards = self.card_payments(months) if include_cards else []
        c1 = sum(c["amount"] for c in cards if c["check"] == 1)
        c2 = sum(c["amount"] for c in cards if c["check"] == 2)
        cflex = sum(c["amount"] for c in cards if c["check"] not in (1, 2))
        first = sum(r["first"] for r in rows) + c1 + cflex / 2
        second = sum(r["second"] for r in rows) + c2 + cflex / 2
        return {"rows": rows, "cards": cards,
                "cards_first": c1 + cflex / 2, "cards_second": c2 + cflex / 2,
                "cards_total": c1 + c2 + cflex,
                "accounts_first": sum(r["first"] for r in rows),
                "accounts_second": sum(r["second"] for r in rows),
                "total": first + second, "first": first, "second": second}

    def income_at(self, month_idx):
        v = self.income_base()
        for b in self.settings.get("income_bumps", []):
            if month_idx >= int(b.get("from_month", 1)):
                v += float(b.get("amount", 0))
        return v

    def free_cash(self, month_idx=1, months=None, before_savings=False):
        """
        What's left for debt. Savings contributions are real money leaving the
        account, so unless you ask for the before-savings figure they are
        deducted here too — otherwise the Savings tab and the Plan tab would
        both spend the same dollar.
        """
        s = self.settings
        spend = self.planned_spend(months)
        v = self.income_at(month_idx) - self.bills_total() - s["rent"] - spend
        if not before_savings:
            v -= self.goals_monthly()
        return v


def clean_merchant(desc: str) -> str:
    d = re.sub(r"\b\d{4,}\b", "", desc)
    d = re.sub(r"\b(AplPay|TST\*|SQ \*|PAYPAL \*|CTLP\*|VSI\*|KPAY\*)\s*", "", d, flags=re.I)
    d = re.sub(r"\s{2,}", " ", d)
    d = re.sub(r"\s+(MOUS|CAUS|WAUS|NCUS|TXUS|ILUS|NYUS|HKG|HK|MO|CA|WA|IL|TX|NV)\s*$", "", d)
    return d.strip(" -*")[:38] or desc[:38]


# ───────────────────────────────────────────────────────────────────────────
#  PAYOFF ENGINE
# ───────────────────────────────────────────────────────────────────────────

MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_label(start_iso, idx):
    y, m, _ = (int(x) for x in start_iso.split("-"))
    t = (m - 1) + (idx - 1)
    return f"{MONTHS_ABBR[t % 12]} {y + t // 12}"


def months_until(start_iso, end_iso):
    """
    Months from `start` through `end`, inclusive. Returns 0 when `end` has
    already passed — a promo that expired belongs on the ordinary avalanche at
    its reverted rate, not on a one-month rescue plan.
    """
    y1, m1, _ = (int(x) for x in start_iso.split("-"))
    y2, m2, _ = (int(x) for x in end_iso.split("-"))
    return max((y2 - y1) * 12 + (m2 - m1) + 1, 0)


def project(store: Store, max_months=600):
    """
    Avalanche by APR, with one override: any card carrying a 0%/promo rate gets a
    reserved slice each month so it lands at zero before the promo expires.
    Returns dict with rows, interest, months, warnings.
    """
    debts = [d for d in store.debts if d.include and d.balance > 0]
    if not debts:
        return {"rows": [], "interest": 0.0, "months": 0, "warnings": [], "ok": True}

    start = store.settings.get("start", date.today().replace(day=1).isoformat())
    bal = {d.name: float(d.balance) for d in debts}
    mn = {d.name: float(d.minimum) for d in debts}
    base_apr = {d.name: float(d.apr) for d in debts}
    promo_n, promo_dead = {}, set()
    for d in debts:
        if d.promo_until:
            n = months_until(start, d.promo_until)
            if n <= 0:
                promo_dead.add(d.name)      # already expired
            else:
                promo_n[d.name] = n

    # If rent is still being charged to a card, the plan must show the balance
    # GROWING by that much every month — otherwise it quietly models a world
    # where the advice has already been taken.
    rent_card = None
    if store.settings.get("rent_on_card") and store.settings.get("rent", 0) > 0:
        live_apr = [d for d in debts]
        rent_card = max(live_apr, key=lambda d: d.apr).name if live_apr else None

    windfalls = {}
    for w in store.settings.get("windfalls", []):
        i = int(w.get("month", 1))
        windfalls[i] = windfalls.get(i, 0.0) + float(w.get("amount", 0))

    rows, interest, m = [], 0.0, 0
    cleared = {}
    stalled = 0
    # spending and bills don't change month to month, so cost them once
    _fixed = (store.bills_total() + store.settings["rent"]
              + store.planned_spend() + store.goals_monthly())
    if rent_card:
        # it isn't leaving the bank account — it's landing on the card instead
        _fixed -= store.settings["rent"]

    while any(v > 0.005 for v in bal.values()) and m < max_months:
        m += 1
        apr = {}
        for d in debts:
            n = d.name
            if n in promo_dead:
                apr[n] = d.promo_apr_after or base_apr[n] or 0.2799
            elif n in promo_n:
                apr[n] = base_apr[n] if m <= promo_n[n] else (
                    d.promo_apr_after or base_apr[n] or 0.2799)
            else:
                apr[n] = base_apr[n]
        if rent_card and rent_card in bal:
            bal[rent_card] += store.settings["rent"]
        for n in bal:
            if bal[n] > 0:
                i = bal[n] * apr[n] / 12
                bal[n] += i
                interest += i

        pool = (store.income_at(m) - _fixed) + windfalls.get(m, 0.0)
        if pool <= 0:
            stalled += 1
            rows.append({"m": m, "label": month_label(start, m),
                         "bal": dict(bal), "paid": {},
                         "total": sum(max(v, 0) for v in bal.values())})
            if stalled > 6:
                break
            continue
        stalled = 0
        paid = {}

        def pay(n, amt):
            nonlocal pool
            amt = min(amt, bal[n], pool)
            if amt <= 0:
                return
            bal[n] -= amt
            pool -= amt
            paid[n] = paid.get(n, 0.0) + amt

        for n in bal:
            if bal[n] > 0:
                pay(n, mn[n])
        # promo reserve
        for n, endm in sorted(promo_n.items(), key=lambda x: x[1]):
            if bal.get(n, 0) > 0 and m <= endm:
                left = endm - m + 1
                pay(n, bal[n] / max(left, 1))
        # avalanche the remainder
        for n in sorted([k for k in bal if bal[k] > 0], key=lambda k: -apr[k]):
            if pool <= 0:
                break
            pay(n, pool)

        for n in bal:
            if n not in cleared and bal[n] <= 0.005:
                cleared[n] = m
        rows.append({"m": m, "label": month_label(start, m), "bal": dict(bal),
                     "paid": paid, "total": sum(max(v, 0) for v in bal.values())})

    warnings = []
    for n, endm in promo_n.items():
        c = cleared.get(n)
        if c is None or c > endm:
            warnings.append(
                f"{n} will NOT be paid off before its 0% rate ends "
                f"({month_label(start, endm)}). Free up about "
                f"${bal.get(n, 0) / max(endm, 1):,.0f}/mo more, or move that balance again.")
    done = all(v <= 0.005 for v in bal.values())
    if not done:
        interest = None      # a runaway figure is worse than no figure
    if not done:
        warnings.append(
            "At this level of spending the balances never clear — "
            "there isn't enough free cash each month. Increase your cuts.")
    return {"rows": rows, "interest": interest, "months": m if done else 0,
            "warnings": warnings, "ok": done, "cleared": cleared,
            "rent_on_card": rent_card}


def scenarios(store: Store, levels=(0, 100, 200, 300, 400, 500, 600, 700, 800)):
    saved = dict(store.cuts)
    base_total = store.spend_total()
    out = []
    for lv in levels:
        store.cuts = spread_cut(store, lv)
        r = project(store)
        out.append({
            "cut": lv,
            "toward_debt": store.free_cash(1),
            "months": r["months"],
            "when": month_label(store.settings["start"], r["months"]) if r["months"] else "never",
            "interest": r["interest"],
            "ok": not r["warnings"],
        })
    store.cuts = saved
    return out


def spread_cut(store: Store, total, spare=("Groceries & household",)):
    by = store.spend_by_category()
    flex = {k: v for k, v in by.items() if k not in spare and v > 0}
    s = sum(flex.values()) or 1
    return {k: min(v, total * v / s) for k, v in flex.items()}


# ───────────────────────────────────────────────────────────────────────────
#  ALERTS  — the things worth knowing every time you open the app
# ───────────────────────────────────────────────────────────────────────────

def _m(v):
    return f"${v:,.0f}"


def _m2(v):
    return f"${v:,.2f}"


def alerts(store: "Store"):
    """Returns [(level, headline, detail)] — level is 'bad' | 'warn' | 'good'."""
    out = []
    live = [d for d in store.debts if d.include and d.balance > 0]
    fc = store.free_cash(1)

    if fc < 0:
        out.append(("bad", f"You're {_m2(abs(fc))} short every month",
                    "Income minus bills, rent and spending is negative, so balances "
                    "grow no matter what you pay. Trim a category on the "
                    "Spending & cuts tab until this turns positive."))
    elif fc < 200:
        out.append(("warn", f"Only {_m2(fc)} free each month",
                    "That barely covers minimums. A single surprise puts you backwards."))

    if store.settings.get("rent_on_card"):
        top = max(live, key=lambda d: d.apr, default=None)
        if top:
            r = store.settings["rent"]
            bal = 0.0
            for _ in range(12):
                bal = (bal + r) * (1 + top.apr / 12)
            out.append(("bad", f"Rent is going on {top.name} at {top.apr*100:.2f}%",
                        f"Charging {_m(r)} a month and carrying it costs about "
                        f"{_m(bal - r*12)} a year in interest on rent alone. "
                        "Pay it from checking instead, then untick 'rent goes on a card' "
                        "on the Debts tab."))

    for d in live:
        if d.promo_until:
            n = months_until(store.settings["start"], d.promo_until)
            need = d.balance / max(n, 1)
            out.append(("warn", f"{d.name}: 0% ends {d.promo_until}",
                        f"{_m(d.balance)} has to be gone in {n} months — about "
                        f"{_m(need)}/mo. After that it reverts to "
                        f"{(d.promo_apr_after or 0.2799)*100:.2f}%."))
        if d.limit and d.balance / d.limit > 0.5:
            out.append(("warn",
                        f"{d.name} is {d.balance/d.limit*100:.0f}% of its limit",
                        f"{_m(d.balance)} of {_m(d.limit)}. Anything over "
                        "30% drags your credit score down."))

    if live:
        top = max(live, key=lambda d: d.apr)
        out.append(("warn" if fc <= 0 else "good",
                    f"Attack {top.name} first — {top.apr*100:.2f}%",
                    f"It costs {_m2(top.balance*top.apr/12)} a month just to "
                    "carry. Every spare dollar goes here until it's gone."))

    cheap = [d for d in live if d.apr < 0.10 and not d.promo_until]
    for d in cheap:
        out.append(("good", f"Don't rush {d.name} — only {d.apr*100:.2f}%",
                    f"It costs {_m2(d.balance*d.apr/12)} a month. Minimums only "
                    "until the expensive cards are gone."))
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  SAVINGS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Goal:
    name: str
    target: float
    saved: float = 0.0
    monthly: float = 0.0
    apy: float = 0.0        # annual yield on the balance, e.g. 0.04
    note: str = ""
    priority: int = 1


def months_to_goal(goal: "Goal", monthly=None):
    """Months until `saved` reaches `target`. None if it never gets there."""
    m = goal.monthly if monthly is None else monthly
    if goal.saved >= goal.target:
        return 0
    if m <= 0 and goal.apy <= 0:
        return None
    bal, r, n = goal.saved, goal.apy / 12, 0
    while bal < goal.target and n < 1200:
        bal = bal * (1 + r) + m
        n += 1
    return n if bal >= goal.target else None


def goal_projection(goal: "Goal", months=60, monthly=None):
    m = goal.monthly if monthly is None else monthly
    bal, r, out = goal.saved, goal.apy / 12, []
    for i in range(1, months + 1):
        bal = bal * (1 + r) + m
        out.append((i, bal))
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  MORTGAGE  &  AFFORDABILITY
# ═══════════════════════════════════════════════════════════════════════════
#
#  Reference figures gathered 2026-08-15 (see the Home tab for sources):
#    Freddie Mac PMMS 8/13/26 : 30-yr 6.67% · 15-yr 5.96%
#    Conventional PMI          : 0.30%–1.50% of the loan per year
#    FHA                       : 1.75% upfront + 0.55% annual (<5% down)
#    Wentzville MO             : 1.31% effective property tax
#    Missouri home insurance   : ~$3,940/yr average
#    Conforming limit 2026     : ~$806,500
#    DTI benchmarks            : 28% front-end / 36% back-end; up to 45–50%

MORTGAGE_DEFAULTS = {
    "apr30": 0.0667, "apr15": 0.0596,
    "tax_rate": 0.0131,          # Wentzville effective rate
    "insurance_annual": 3940.0,  # Missouri average
    "pmi_rate": 0.0075,          # mid of the 0.3%-1.5% conventional band
    "fha_upfront": 0.0175, "fha_annual": 0.0055,
    "front_end": 0.28, "back_end": 0.36,
    "conforming_limit": 806500.0,
}


def monthly_pi(principal, apr, years):
    """Principal + interest payment."""
    if principal <= 0:
        return 0.0
    r = apr / 12
    n = int(years * 12)
    if r <= 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def piti(price, down, apr, years, tax_rate, ins_annual, pmi_rate, hoa=0.0):
    """Full monthly payment, broken out."""
    loan = max(price - down, 0.0)
    ltv = loan / price if price > 0 else 0.0
    pi = monthly_pi(loan, apr, years)
    tax = price * tax_rate / 12
    ins = ins_annual / 12
    pmi = (loan * pmi_rate / 12) if ltv > 0.80 else 0.0
    return {"loan": loan, "ltv": ltv, "pi": pi, "tax": tax, "ins": ins,
            "pmi": pmi, "hoa": hoa, "total": pi + tax + ins + pmi + hoa}


def max_payment(gross_monthly, other_debts, front=0.28, back=0.36):
    """The smaller of the front-end and back-end caps."""
    return max(0.0, min(gross_monthly * front, gross_monthly * back - other_debts))


def affordability(gross_monthly, down, other_debts, apr, years=30,
                  tax_rate=None, ins_annual=None, pmi_rate=None, hoa=0.0,
                  front=0.28, back=0.36):
    d = MORTGAGE_DEFAULTS
    tax_rate = d["tax_rate"] if tax_rate is None else tax_rate
    ins_annual = d["insurance_annual"] if ins_annual is None else ins_annual
    pmi_rate = d["pmi_rate"] if pmi_rate is None else pmi_rate
    front_cap = gross_monthly * front
    back_cap = max(0.0, gross_monthly * back - other_debts)
    budget = min(front_cap, back_cap)
    lo, hi = down, max(down + 10.0, 5_000_000.0)
    for _ in range(200):
        mid = (lo + hi) / 2
        if piti(mid, down, apr, years, tax_rate, ins_annual, pmi_rate, hoa)["total"] > budget:
            hi = mid
        else:
            lo = mid
    price = max(lo, down)
    det = piti(price, down, apr, years, tax_rate, ins_annual, pmi_rate, hoa)
    return {"price": price, "budget": budget, "front_cap": front_cap,
            "back_cap": back_cap, "binding": ("your other debts" if back_cap < front_cap
                                              else "the 28% housing rule"),
            **det}


def pmi_timeline(price, down, apr, years, pmi_rate):
    """
    When PMI ends, under the Homeowners Protection Act:
      · you may REQUEST cancellation at 80% of ORIGINAL value
      · the servicer must AUTO-terminate at 78% of original value
      · it must end at the loan's midpoint regardless (month 180 of a 30-yr)
    Returns the months, the monthly cost, and the total you'd pay each way.
    """
    loan = max(price - down, 0.0)
    if price <= 0 or loan <= 0 or loan / price <= 0.80:
        return {"applies": False, "monthly": 0.0}
    r = apr / 12
    n = int(years * 12)
    pay = monthly_pi(loan, apr, years)
    monthly = loan * pmi_rate / 12
    bal = loan
    req = auto = None
    for m in range(1, n + 1):
        bal = bal + bal * r - pay
        if req is None and bal <= 0.80 * price:
            req = m
        if auto is None and bal <= 0.78 * price:
            auto = m
            break
    mid = n // 2
    auto_eff = min(auto or mid, mid)
    req_eff = min(req or mid, mid)
    return {"applies": True, "monthly": monthly, "loan": loan,
            "ltv": loan / price, "request_month": req_eff, "auto_month": auto_eff,
            "midpoint_month": mid,
            "total_if_request": monthly * req_eff,
            "total_if_auto": monthly * auto_eff,
            "saved_by_asking": monthly * (auto_eff - req_eff)}


def down_to_avoid_pmi(price):
    return price * 0.20


def amortization(principal, apr, years, extra=0.0):
    r, n = apr / 12, int(years * 12)
    pay = monthly_pi(principal, apr, years) + extra
    bal, rows, tot_int = principal, [], 0.0
    for m in range(1, n + 1):
        i = bal * r
        p = min(pay - i, bal)
        bal -= p
        tot_int += i
        rows.append((m, p, i, max(bal, 0.0)))
        if bal <= 0.005:
            break
    return {"rows": rows, "months": len(rows), "interest": tot_int,
            "payment": monthly_pi(principal, apr, years)}


# ═══════════════════════════════════════════════════════════════════════════
#  PAYDAY PLAN — a printable page, not a spreadsheet
# ═══════════════════════════════════════════════════════════════════════════

def payday_plan_html(store, title="Payday plan"):
    """
    One page you can print and stick on the fridge: for each paycheck, exactly
    what to move where, in what order, and what is left afterwards.
    """
    plan = store.paycheck_plan()
    pay = store.settings.get("paycheck", 0.0)
    money = lambda v: f"${v:,.2f}"
    m0 = lambda v: f"${v:,.0f}"
    P = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
         "#4a3aa7", "#008300", "#e34948"]

    def lines_for(which):
        """(label, detail, amount, colour) for one paycheck."""
        out = []
        for i, r in enumerate(plan["rows"]):
            a = r["account"]
            col = P[(a.color if a.color else i) % len(P)]
            amt = r["first"] if which == 1 else r["second"]
            if amt <= 0.004:
                continue
            bits = []
            for nm, v, ch in r["pinned"]:
                if ch == which:
                    bits.append(f"{nm} {money(v)} (in full)")
            share = (r["flexible"] * (r["pct_first"] / 100 if which == 1
                                      else r["pct_second"] / 100))
            if share > 0.004:
                pct = r["pct_first"] if which == 1 else r["pct_second"]
                bits.append(f"{pct:.0f}% of the {money(r['flexible'])} that splits")
            out.append((a.name, " · ".join(bits) or "—", amt, col, "account"))
        for j, c in enumerate(plan["cards"]):
            if c["check"] == which:
                amt = c["amount"]
            elif c["check"] == 0:
                amt = c["amount"] / 2
            else:
                continue
            if amt <= 0.004:
                continue
            note = ("split evenly across both checks" if c["check"] == 0
                    else "whole payment from this check")
            out.append((c["name"], f"{c['apr']*100:.2f}% · {note}", amt,
                        P[7], "card"))
        return out

    def block(which):
        rows = lines_for(which)
        tot = sum(r[2] for r in rows)
        left = pay - tot
        body = "".join(
            f'''<tr class="{kind}">
              <td class="sw"><i style="background:{col}"></i></td>
              <td class="nm">{nm}</td>
              <td class="dt">{det}</td>
              <td class="am">{money(amt)}</td>
            </tr>''' for nm, det, amt, col, kind in rows) or (
            '<tr><td colspan="4" class="dt">Nothing assigned to this check.</td></tr>')
        state = "ok" if left >= 0 else "over"
        leftline = (f"{money(left)} left over" if left >= 0
                    else f"{money(-left)} MORE than this paycheck")
        return f'''
        <section class="check">
          <header>
            <span class="tag">{'1st' if which == 1 else '2nd'} paycheck</span>
            <h2>{money(tot)}</h2>
            <p class="of">out of {money(pay)} take-home</p>
          </header>
          <table>{body}</table>
          <footer class="{state}">{leftline}</footer>
        </section>'''

    yearly = plan["total"] * 12
    per26 = yearly / 26 if yearly else 0
    unassigned = sum(store.unassigned_categories().values()) + \
        sum(store.unassigned_bills().values())
    warn = ""
    if unassigned > 0:
        warn = ('<div class="warn"><b>' + money(unassigned) + " a month isn't "
                "assigned to any account.</b> It still leaves your bank account "
                "— it just isn't budgeted to a card yet.</div>")

    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>{title}</title><style>
:root{{--ink:#14140f;--ink2:#54534e;--muted:#7d7b74;--line:#dedcd4;
--card:#fff;--bg:#f4f4f1;--good:#146c2e;--bad:#b3261e;--warn:#8a5a00;
--warnbg:#fdf6e8;--goodbg:#eef7ee;--badbg:#fdf1f0;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:15px;
line-height:1.5;padding:34px 22px 60px}}
.wrap{{max-width:1000px;margin:0 auto}}
h1{{font-size:30px;margin:0 0 4px;letter-spacing:-.02em}}
.sub{{color:var(--ink2);margin:0 0 4px}}
.stamp{{color:var(--muted);font-size:13px;margin:0 0 24px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
@media (max-width:780px){{.grid{{grid-template-columns:1fr}}}}
.check{{background:var(--card);border:1px solid var(--line);border-radius:14px;
overflow:hidden;break-inside:avoid}}
.check header{{padding:18px 20px 12px;border-bottom:1px solid var(--line)}}
.tag{{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
color:#fff;background:var(--ink);padding:4px 10px;border-radius:20px}}
.check h2{{font-size:34px;margin:12px 0 0;letter-spacing:-.02em}}
.of{{margin:2px 0 0;color:var(--muted);font-size:13px}}
table{{width:100%;border-collapse:collapse}}
td{{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:none}}
tr.card td{{background:#fbfaf8}}
.sw{{width:22px}} .sw i{{display:block;width:5px;height:22px;border-radius:3px}}
.nm{{font-weight:650;white-space:nowrap}}
.dt{{color:var(--muted);font-size:12px}}
.am{{text-align:right;font-weight:700;white-space:nowrap;
font-variant-numeric:tabular-nums}}
footer{{padding:12px 20px;font-weight:650}}
footer.ok{{background:var(--goodbg);color:var(--good)}}
footer.over{{background:var(--badbg);color:var(--bad)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
gap:12px;margin:22px 0}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px 16px}}
.tile b{{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);margin-bottom:4px}}
.tile span{{font-size:23px;font-weight:700;letter-spacing:-.02em}}
.tile em{{display:block;font-style:normal;color:var(--ink2);font-size:12px;
margin-top:3px}}
.warn{{background:var(--warnbg);border:1px solid #eda100;border-left-width:5px;
border-radius:0 10px 10px 0;padding:13px 16px;margin:18px 0;color:var(--ink2)}}
.warn b{{color:var(--warn)}}
.note{{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;margin:18px 0;color:var(--ink2);font-size:14px}}
.note h3{{margin:0 0 6px;font-size:14px;color:var(--ink)}}
.foot{{color:var(--muted);font-size:12px;margin-top:26px}}
@media print{{body{{background:#fff;padding:0}}.check{{border-color:#bbb}}
.tiles{{page-break-inside:avoid}}}}
</style></head><body><div class="wrap">
<h1>{title}</h1>
<p class="sub">What to move out of each paycheck, and what is left afterwards.</p>
<p class="stamp">Take-home {money(pay)} per check · {len(plan['rows'])} spending
account(s) · {len(plan['cards'])} card payment(s)</p>
{warn}
<div class="grid">{block(1)}{block(2)}</div>
<div class="tiles">
  <div class="tile"><b>Every month</b><span>{m0(plan['total'])}</span>
    <em>accounts {m0(plan['accounts_first'] + plan['accounts_second'])} ·
    cards {m0(plan['cards_total'])}</em></div>
  <div class="tile"><b>1st check</b><span>{m0(plan['first'])}</span>
    <em>{m0(max(pay - plan['first'], 0))} left</em></div>
  <div class="tile"><b>2nd check</b><span>{m0(plan['second'])}</span>
    <em>{m0(max(pay - plan['second'], 0))} left</em></div>
  <div class="tile"><b>Across a year</b><span>{m0(yearly)}</span>
    <em>{m0(per26)} per check over 26 checks</em></div>
</div>
<div class="note">
  <h3>You are paid every two weeks, not twice a month</h3>
  That is 26 checks a year, not 24. Funding {money(plan['total'] / 2)} per check
  puts away {money(plan['total'] / 2 * 26)} against {money(yearly)} of real cost —
  so twice a year a whole check is spare. Either move {money(per26)} per check and
  stay exactly level, or keep funding half and send those two extra checks
  straight at the cards. While the cards are alive, the second is worth more.
</div>
<p class="foot">Generated by Payoff Planner from your own statements. Amounts for
cards come from the current payoff plan and change as balances do.</p>
</div></body></html>'''
