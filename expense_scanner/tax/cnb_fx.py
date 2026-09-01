import json
import ssl
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

import certifi

_CACHE: Dict[str, Dict[str, Tuple[float, int]]] = {}


def _rates_for_date(iso_date: str) -> Dict[str, Tuple[float, int]]:
    if iso_date in _CACHE:
        return _CACHE[iso_date]
    url = f"https://api.cnb.cz/cnbapi/exrates/daily?date={iso_date}&lang=EN"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "expense-scanner/1"},
    )
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        payload = json.load(resp)
    out: Dict[str, Tuple[float, int]] = {}
    for row in payload.get("rates") or []:
        code = row.get("currencyCode")
        if not code:
            continue
        amt = row.get("amount")
        nom = int(amt) if isinstance(amt, int) and amt > 0 else 1
        out[str(code).upper()] = (float(row["rate"]), nom)
    _CACHE[iso_date] = out
    return out


def foreign_to_czk(
    amount: float,
    currency: Optional[str],
    iso_date: str,
) -> Optional[float]:
    ccy = (currency or "").strip().upper()
    if not ccy or ccy == "UNK":
        return None
    if ccy == "CZK":
        return round(float(amount), 2)
    try:
        table = _rates_for_date(iso_date)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        KeyError,
    ):
        return None
    row = table.get(ccy)
    if not row:
        return None
    rate, nom = row
    return round(float(amount) * rate / nom, 2)
