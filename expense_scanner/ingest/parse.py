import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


_DATE_LINE_HINTS = re.compile(
    r"(datum|date|vystaven|invoice|rechnung|faktura|purchase|beleg|issued|due)",
    re.I,
)
_TOTAL_LINE_HINTS = re.compile(
    r"(zaplac|zaplat|celkem|součet|celkov|gesamt|summe|total|amount|saldo|balance|"
    r"betrag|k\s*zaplat|shrnut|SUM\s*EUR|eur\s*total)",
    re.I,
)

_DATE_DDMMYYYY = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b",
)
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_EN_MONTH = re.compile(
    r"\b("
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    r")\s+(\d{1,2}),\s*(\d{4})\b",
    re.I,
)

_MONTH_EN_TO_INT = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_AMOUNTS = re.compile(
    r"(?<![\d.])(\d{1,3}(?:\.\d{3})+|\d+)\s*([,\.])(\d{2})\b(?!\s*%)",
)
_USD_AMOUNT = re.compile(r"(?<!\S)\$(\d{1,3}(?:,\d{3})*)\.(\d{2})\b")


_VAT_KW = re.compile(
    r"(?i)\b(dph|daň|vat|mwst\.?|mehrwertsteuer|u\.?\s*st\.?|ust\.?)\b"
)

_VAT_PCT = re.compile(r"(?<![\d.])(5|7|9|10|12|14|15|19|21|23)\s*%", re.I)

_PLAUSIBLE_VAT_RATES = frozenset(
    {5.0, 7.0, 9.0, 10.0, 12.0, 14.0, 15.0, 19.0, 21.0, 23.0}
)


@dataclass
class ParsedReceipt:
    receipt_date: Optional[date]
    total: Optional[float]
    currency: Optional[str]
    merchant_hint: Optional[str]
    vat_amount: Optional[float]
    vat_rate: Optional[float]


def _parse_amount_groups(m: Any) -> float:
    whole, sep, frac = m.group(1), m.group(2), m.group(3)
    if sep == ",":
        whole_norm = whole.replace(".", "")
        return float(f"{whole_norm}.{frac}")
    whole_norm = whole.replace(",", "")
    return float(f"{whole_norm}.{frac}")


def _currency_near(text: str, start: int, end: int) -> Optional[str]:
    window = text[max(0, start - 12) : min(len(text), end + 12)]
    if re.search(r"\$|\bUSD|usd\b", window):
        return "USD"
    if re.search(r"\b€|EUR|eur\b", window):
        return "EUR"
    if re.search(r"\bKč|CZK|kc\b", window, re.I):
        return "CZK"
    return None


def _parse_date_from_text(text: str) -> Optional[date]:
    best: Optional[Tuple[int, date]] = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        prev_blob = " ".join(lines[max(0, i - 2) : i + 1])
        for m in _DATE_EN_MONTH.finditer(line):
            mk = m.group(1).lower()
            mo = _MONTH_EN_TO_INT.get(mk) or _MONTH_EN_TO_INT.get(mk[:3])
            if mo is None:
                continue
            d, y = int(m.group(2)), int(m.group(3))
            try:
                candidate = date(y, mo, d)
            except ValueError:
                continue
            score = 5
            if _DATE_LINE_HINTS.search(prev_blob):
                score += 15
            pos = sum(len(lines[j]) + 1 for j in range(i)) + m.start()
            score += min(max(0, len(text) - pos), 5000)
            if best is None or score > best[0]:
                best = (score, candidate)
    for m in _DATE_DDMMYYYY.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        score = 0
        if _DATE_LINE_HINTS.search(line):
            score += 10
        score += min(len(text) - m.start(), 5000)
        try:
            candidate = date(y, mo, d)
        except ValueError:
            continue
        if best is None or score > best[0]:
            best = (score, candidate)
    if best:
        return best[1]
    m = _DATE_ISO.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _parse_total(text: str) -> Tuple[Optional[float], Optional[str]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hinted: List[Tuple[float, Optional[str]]] = []
    for i, line in enumerate(lines):
        if not _TOTAL_LINE_HINTS.search(line):
            continue
        chunk = " ".join(lines[i : min(len(lines), i + 10)])
        for m in _AMOUNTS.finditer(chunk):
            hinted.append((_parse_amount_groups(m), _currency_near(chunk, m.start(), m.end())))
        for m in _USD_AMOUNT.finditer(chunk):
            whole = m.group(1).replace(",", "")
            hinted.append((float(f"{whole}.{m.group(2)}"), "USD"))
    if hinted:
        amt, cur = max(hinted, key=lambda x: x[0])
        if amt > 0:
            return amt, cur

    amounts: List[Tuple[float, Optional[str]]] = []
    for m in _AMOUNTS.finditer(text):
        val = _parse_amount_groups(m)
        if 0.01 <= val <= 2_000_000:
            amounts.append((val, _currency_near(text, m.start(), m.end())))
    for m in _USD_AMOUNT.finditer(text):
        whole = m.group(1).replace(",", "")
        val = float(f"{whole}.{m.group(2)}")
        if 0.01 <= val <= 2_000_000:
            amounts.append((val, "USD"))
    if amounts:
        amt, cur = max(amounts, key=lambda x: x[0])
        return amt, cur
    return None, None


def _parse_vat_amount(text: str, total: Optional[float]) -> Optional[float]:
    candidates: List[float] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln or not _VAT_KW.search(ln):
            continue
        if _TOTAL_LINE_HINTS.search(ln) and not re.search(
            r"(?i)\b(základ|bez\s*dph|netto)\b", ln
        ):
            continue
        pct_only = re.search(r"(?i)\b\d{1,2}\s*%", ln)
        has_amt = bool(_AMOUNTS.search(ln) or _USD_AMOUNT.search(ln))
        if pct_only and not has_amt:
            continue
        for m in _AMOUNTS.finditer(ln):
            v = _parse_amount_groups(m)
            if 0.01 <= v <= 999999:
                candidates.append(v)
        for m in _USD_AMOUNT.finditer(ln):
            whole = m.group(1).replace(",", "")
            v = float(f"{whole}.{m.group(2)}")
            if 0.01 <= v <= 999999:
                candidates.append(v)
    if not candidates:
        return None
    tmax = float(total) if isinstance(total, (int, float)) and total and total > 0 else None
    if tmax:
        plausible = [c for c in candidates if c <= tmax * 0.45 + 0.005]
        pool = plausible if plausible else candidates
        return max(pool)
    return max(candidates)


def _parse_vat_rate_from_lines(text: str) -> Optional[float]:
    scored: List[Tuple[float, float]] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln or not _VAT_KW.search(ln):
            continue
        has_amt = bool(_AMOUNTS.search(ln) or _USD_AMOUNT.search(ln))
        for m in _VAT_PCT.finditer(ln):
            r = float(m.group(1))
            if r not in _PLAUSIBLE_VAT_RATES:
                continue
            scored.append((r, 3.0 + (2.0 if has_amt else 0.0)))
    if scored:
        by_rate: Dict[float, float] = {}
        for r, s in scored:
            by_rate[r] = by_rate.get(r, 0.0) + s
        return max(by_rate.keys(), key=lambda x: (by_rate[x], x))
    rates_loose: List[float] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln or _TOTAL_LINE_HINTS.search(ln):
            continue
        if not _VAT_KW.search(ln):
            continue
        for m in _VAT_PCT.finditer(ln):
            r = float(m.group(1))
            if r in _PLAUSIBLE_VAT_RATES:
                rates_loose.append(r)
    if rates_loose:
        r, _ = Counter(rates_loose).most_common(1)[0]
        return r
    return None


def _infer_vat_rate_from_amounts(vat_amt: float, total: float) -> Optional[float]:
    if total <= 0 or vat_amt <= 0 or total <= vat_amt:
        return None
    net = total - vat_amt
    if net <= 0:
        return None
    implied = (vat_amt / net) * 100.0
    best: Optional[Tuple[float, float]] = None
    for pr in sorted(_PLAUSIBLE_VAT_RATES, reverse=True):
        d = abs(implied - pr)
        if best is None or d < best[0]:
            best = (d, pr)
    if best and best[0] < 1.25:
        return best[1]
    return None


def _parse_vat_fields(
    text: str, total: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    vat_amt = _parse_vat_amount(text, total)
    vat_rate = _parse_vat_rate_from_lines(text)
    t = float(total) if isinstance(total, (int, float)) and total and total > 0 else None

    if vat_amt is None and vat_rate is not None and t is not None:
        vat_amt = round(t * vat_rate / (100.0 + vat_rate), 2)

    if vat_amt is not None and vat_rate is None and t is not None:
        vat_rate = _infer_vat_rate_from_amounts(vat_amt, t)

    return vat_amt, vat_rate


def _merchant_hint(text: str) -> Optional[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[:6]:
        if len(ln) < 3:
            continue
        if re.fullmatch(r"[\d\s\.\-/:]+", ln):
            continue
        return ln[:120] if len(ln) > 120 else ln
    return None


def parse_receipt_text(text: str) -> ParsedReceipt:
    cleaned = text if text else ""
    receipt_date = _parse_date_from_text(cleaned)
    total, currency = _parse_total(cleaned)
    vat_a, vat_r = _parse_vat_fields(cleaned, total)
    return ParsedReceipt(
        receipt_date=receipt_date,
        total=total,
        currency=currency,
        merchant_hint=_merchant_hint(cleaned),
        vat_amount=vat_a,
        vat_rate=vat_r,
    )
