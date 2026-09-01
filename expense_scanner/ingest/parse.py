import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


# Dates
_DATE_LINE_HINTS = re.compile(
    r"(datum|date|vystaven|invoice|rechnung|faktura|purchase|beleg|issued)",
    re.I,
)
_DATE_GOOD = re.compile(
    r"(n[áa]kup|purchase|rechnungsdatum|belegdatum|vystaven|"
    r"date\s*of\s*(purchase|issue)|prodej)",
    re.I,
)
_DATE_BAD = re.compile(
    r"(splatnost|due\b|f[äa]llig|valid|g[üu]ltig|expir|until|"
    r"platnost|best\s*before|mindesthalt|delivery\s*date)",
    re.I,
)
_DATE_DDMMYYYY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_DATE_SLASH = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
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

# Amounts
_THOUSANDS = r"(?:\.\s?|\s|\u00a0|\u202f)"
_AMOUNTS = re.compile(
    rf"(?<![\d.,])(\d{{1,3}}(?:{_THOUSANDS}\d{{3}})+|\d+)\s*([,\.])(\d{{2}})(?!\d)(?!\s*%)(?!\.\d)"
)
_AMOUNT_DASH = re.compile(
    rf"(?<![\d.,])(\d{{1,3}}(?:{_THOUSANDS}\d{{3}})+|\d+)\s*,-"
)
_USD_AMOUNT = re.compile(r"(?<!\S)\$(\d{1,3}(?:,\d{3})*)\.(\d{2})\b")

# Totals
_GRAND_TOTAL_LINE = re.compile(
    r"(?i)("
    r"k\s*[úu]hrad|k\s*zaplac|"
    r"zaplaceno|"
    r"gesamtbetrag|endbetrag|zu\s*zahlen|zahlbetrag|rechnungsbetrag|"
    r"grand\s*total|amount\s*due|amount\s*paid|"
    r"celkem\s*k\s*[úu]hrad"
    r")"
)
_MEDIUM_TOTAL_LINE = re.compile(
    r"(?i)(\bcelkem\b|\bcelkov\w*|\bsou[čc]et\b|\bsumme\b|\btotal\b|\bshrnut)"
)
_NOT_TOTAL_LINE = re.compile(
    r"(?i)("
    r"subtotal|zwischensumme|mezisou[čc]et|"
    r"hotovost|cash\s*(tender|received)|gegeben|gegebenen|"
    r"vr[áa]cen|change\b|tip\b|spropitn|"
    r"celkem\s*dph|total\s*vat|summe\s*mwst|"
    r"bez\s*dph|netto|net\s*total"
    r")"
)

# VAT
_VAT_KW = re.compile(
    r"(?i)\b(dph|daň|vat|mwst\.?|mehrwertsteuer|u\.?\s*st\.?|ust\.?)\b"
)
_VAT_PCT = re.compile(r"(?<![\d.])(5|7|9|10|12|14|15|19|21|23)\s*%", re.I)
_PLAUSIBLE_VAT_RATES = frozenset(
    {5.0, 7.0, 9.0, 10.0, 12.0, 14.0, 15.0, 19.0, 21.0, 23.0}
)

# Merchant
_MERCHANT_SKIP = re.compile(
    r"(?i)(^www\.|^https?://|"
    r"\b(i[cč]o|di[cč]|vat\s*(id|no)|telefon|tel\.|phone|fax|e-?mail)\b|"
    r"^(paragon|doklad|faktura|invoice|rechnung|receipt|tax\s*invoice|"
    r"pokladn|cash\s*receipt|customer\s*copy|copy|účtenka|uctenka|"
    r"datum|date|vystaven|splatnost|issued|beleg)\b)"
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
        whole_norm = re.sub(r"[.\s\u00a0\u202f]", "", whole)
    else:
        whole_norm = re.sub(r"[,\s\u00a0\u202f]", "", whole)
    return float(f"{whole_norm}.{frac}")


def _iter_amounts(text: str) -> List[Tuple[float, Optional[str], int, int]]:
    found: List[Tuple[float, Optional[str], int, int]] = []
    for m in _AMOUNTS.finditer(text):
        found.append(
            (_parse_amount_groups(m), _currency_near(text, m.start(), m.end()), m.start(), m.end())
        )
    for m in _AMOUNT_DASH.finditer(text):
        whole = re.sub(r"[.\s\u00a0\u202f]", "", m.group(1))
        found.append(
            (float(whole), _currency_near(text, m.start(), m.end()), m.start(), m.end())
        )
    for m in _USD_AMOUNT.finditer(text):
        whole = m.group(1).replace(",", "")
        found.append(
            (float(f"{whole}.{m.group(2)}"), "USD", m.start(), m.end())
        )
    return found


def _currency_near(text: str, start: int, end: int) -> Optional[str]:
    window = text[max(0, start - 18) : min(len(text), end + 18)]
    if re.search(r"\$|\bUSD\b", window, re.I):
        return "USD"
    if re.search(r"€|\bEUR\b", window, re.I):
        return "EUR"
    if re.search(r"Kč|\bCZK\b|\bKc\b", window, re.I):
        return "CZK"
    if re.search(r"\bPLN\b|zł", window, re.I):
        return "PLN"
    return None


def _guess_currency(text: str, cur: Optional[str]) -> Optional[str]:
    if cur:
        return cur
    counts = {"CZK": 0, "EUR": 0, "USD": 0, "PLN": 0}
    if re.search(r"Kč|\bCZK\b", text, re.I):
        counts["CZK"] += 2
    if re.search(r"€|\bEUR\b", text, re.I):
        counts["EUR"] += 2
    if re.search(r"\$|\bUSD\b", text, re.I):
        counts["USD"] += 1
    if re.search(r"\bPLN\b|zł", text, re.I):
        counts["PLN"] += 2
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] else None


def _safe_date(y: int, mo: int, d: int) -> Optional[date]:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _line_at(text: str, pos: int) -> str:
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def _score_date(pos: int, line: str) -> int:
    score = 8
    if _DATE_GOOD.search(line):
        score += 22
    elif _DATE_LINE_HINTS.search(line):
        score += 8
    if _DATE_BAD.search(line):
        score -= 28
    score += max(0, 2500 - pos) // 250
    return score


def _parse_date_from_text(text: str) -> Optional[date]:
    best: Optional[Tuple[int, date]] = None

    def consider(score: int, candidate: Optional[date]) -> None:
        nonlocal best
        if candidate is None:
            return
        if best is None or score > best[0]:
            best = (score, candidate)

    for m in _DATE_EN_MONTH.finditer(text):
        mk = m.group(1).lower()
        mo = _MONTH_EN_TO_INT.get(mk) or _MONTH_EN_TO_INT.get(mk[:3])
        if mo is None:
            continue
        line = _line_at(text, m.start())
        consider(
            _score_date(m.start(), line),
            _safe_date(int(m.group(3)), mo, int(m.group(2))),
        )
    for m in _DATE_DDMMYYYY.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        line = _line_at(text, m.start())
        consider(_score_date(m.start(), line), _safe_date(y, mo, d))
    for m in _DATE_SLASH.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        line = _line_at(text, m.start())
        consider(_score_date(m.start(), line) - 2, _safe_date(y, mo, d))
    for m in _DATE_ISO.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        line = _line_at(text, m.start())
        consider(_score_date(m.start(), line), _safe_date(y, mo, d))

    if best:
        return best[1]
    return None


def _parse_total(text: str) -> Tuple[Optional[float], Optional[str]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    grand: List[Tuple[float, Optional[str]]] = []
    medium: List[Tuple[float, Optional[str]]] = []
    for i, line in enumerate(lines):
        if _NOT_TOTAL_LINE.search(line):
            continue
        amts = _iter_amounts(line)
        if not amts and i + 1 < len(lines):
            nxt = lines[i + 1]
            if not (
                _NOT_TOTAL_LINE.search(nxt)
                or _GRAND_TOTAL_LINE.search(nxt)
                or _MEDIUM_TOTAL_LINE.search(nxt)
            ):
                amts = _iter_amounts(nxt)
        if not amts:
            continue
        amt, cur, _, _ = amts[-1]
        if amt <= 0:
            continue
        if _GRAND_TOTAL_LINE.search(line):
            grand.append((amt, cur))
        elif _MEDIUM_TOTAL_LINE.search(line):
            medium.append((amt, cur))
    if grand:
        amt, cur = grand[-1]
        return amt, _guess_currency(text, cur)
    if medium:
        amt, cur = medium[-1]
        return amt, _guess_currency(text, cur)

    amounts: List[Tuple[float, Optional[str]]] = []
    for line in lines:
        if _NOT_TOTAL_LINE.search(line) or _DATE_BAD.search(line):
            continue
        for v, c, _, _ in _iter_amounts(line):
            if 0.01 <= v <= 2_000_000:
                amounts.append((v, c))
    if amounts:
        amt, cur = max(amounts, key=lambda x: x[0])
        return amt, _guess_currency(text, cur)
    return None, _guess_currency(text, None)


def _parse_vat_amount(text: str, total: Optional[float]) -> Optional[float]:
    candidates: List[float] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln or not _VAT_KW.search(ln):
            continue
        if _GRAND_TOTAL_LINE.search(ln) and not re.search(
            r"(?i)\b(dph|mwst|vat|základ|zaklad|netto)\b", ln
        ):
            continue
        pct_only = re.search(r"(?i)\b\d{1,2}\s*%", ln)
        has_amt = bool(_iter_amounts(ln))
        if pct_only and not has_amt:
            continue
        for v, _, _, _ in _iter_amounts(ln):
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
        has_amt = bool(_iter_amounts(ln))
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
        if not ln or _GRAND_TOTAL_LINE.search(ln) or _MEDIUM_TOTAL_LINE.search(ln):
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
    for ln in lines[:12]:
        if len(ln) < 3 or len(ln) > 80:
            continue
        if re.fullmatch(r"[\d\s\.\-/:]+", ln):
            continue
        if _MERCHANT_SKIP.search(ln):
            continue
        if _DATE_DDMMYYYY.search(ln) or _DATE_ISO.search(ln) or _DATE_SLASH.search(ln):
            continue
        if _iter_amounts(ln):
            continue
        if not re.search(r"[A-Za-zÁ-žÄÖÜß]", ln):
            continue
        return ln[:120]
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
