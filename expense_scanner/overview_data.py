import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from expense_scanner.categories import load_tax_categories
from expense_scanner.cnb_fx import foreign_to_czk
from expense_scanner.cz_stravne import effective_trip_amounts, load_stravne_meta
from expense_scanner.json_fs import load_json
from expense_scanner.invoice_store import list_income_rows
from expense_scanner.obligations_store import (
    all_obligation_nav_months,
    build_obligations_summary,
)
from expense_scanner.receipt_edit import list_month_data_files
from expense_scanner.travel_store import list_trips

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _amount_in_czk(
    total: float,
    currency: Optional[str],
    valuation_date: Optional[str],
) -> Optional[float]:
    ccy = (currency or "").strip().upper()
    if not ccy or ccy == "UNK":
        return None
    if ccy == "CZK":
        return round(float(total), 2)
    if not valuation_date:
        return None
    return foreign_to_czk(float(total), ccy, valuation_date)


def _receipt_valuation_date(r: Dict[str, Any]) -> Optional[str]:
    for key in ("payment_date", "date"):
        v = r.get(key)
        if isinstance(v, str):
            s = v.strip()
            if _ISO_DATE.match(s):
                return s
    return None

_OVERVIEW_SKIP_NAMES = frozenset(
    {
        "travel_allowances.json",
        "landing_notes.json",
        "merchant_category_rules.json",
        "obligations.json",
        "obligations_summary.json",
    }
)


def _category_labels() -> Dict[str, str]:
    return {c["id"]: c["label_cs"] for c in load_tax_categories()["categories"]}


def _month_sort_key(path: Path) -> str:
    stem = path.stem
    if stem == "unknown":
        return "0000-00-unknown"
    return stem


def _month_stem_sort_key(stem: str) -> str:
    if stem.lower() == "unknown":
        return "0000-00-unknown"
    return stem


def _travel_for_overview(trips: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for t in trips:
        eff = effective_trip_amounts(t)
        stored = t.get("amount_total")
        ccy_stored = (t.get("currency") or "CZK").strip().upper()[:3]
        disp = eff["amount_total"]
        ccy_eff = eff["currency"]
        is_est = eff["from_stravne_table"]
        if isinstance(disp, (int, float)):
            totals[ccy_eff] = totals.get(ccy_eff, 0.0) + float(disp)
        rows.append(
            {
                "id": t.get("id"),
                "purpose": t.get("purpose") or "",
                "country_code": t.get("country_code") or "",
                "destination": t.get("destination") or "",
                "date_from": t.get("date_from"),
                "date_to": t.get("date_to"),
                "claim_type": t.get("claim_type") or "",
                "amount_total": stored,
                "currency": ccy_stored,
                "amount_effective": disp,
                "currency_effective": ccy_eff,
                "is_stravne_estimate": is_est,
                "stravne_detail_cs": eff.get("stravne_detail_cs"),
                "notes": t.get("notes"),
            }
        )
    rows.sort(
        key=lambda x: (x.get("date_from") or "", x.get("date_to") or ""),
        reverse=True,
    )
    return {
        "trips": rows,
        "totals_by_currency": {k: round(v, 2) for k, v in sorted(totals.items())},
    }


def _income_bucket_month(row: Dict[str, Any]) -> str:
    pm = row.get("paid_month")
    if isinstance(pm, str) and len(pm) >= 7 and pm[4:5] == "-":
        y, m = pm[:4], pm[5:7]
        if y.isdigit() and m.isdigit():
            return f"{y}-{m}"
    inv = row.get("invoice_date")
    if isinstance(inv, str) and len(inv) >= 7 and inv[4:5] == "-":
        return inv[:7]
    return "unknown"


def aggregate_income_for_overview(
    rows: List[Dict[str, Any]],
    expenses_total_czk: float,
) -> Dict[str, Any]:
    grand_ccy: Dict[str, float] = {}
    grand_czk = 0.0
    fx_skip = 0
    pending_count = 0
    pending_ccy: Dict[str, float] = {}
    months: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        t = row.get("amount")
        ccy = (row.get("currency") or "").strip().upper()
        has_amt = isinstance(t, (int, float)) and ccy and ccy != "UNK"

        if not row.get("paid"):
            pending_count += 1
            if has_amt:
                pending_ccy[ccy] = pending_ccy.get(ccy, 0.0) + float(t)
            continue

        if has_amt:
            grand_ccy[ccy] = grand_ccy.get(ccy, 0.0) + float(t)

        czk = row.get("amount_czk")
        if isinstance(czk, (int, float)):
            grand_czk += float(czk)
        elif (
            has_amt
            and ccy != "CZK"
        ):
            fx_skip += 1

        ym = _income_bucket_month(row)
        slot = months.setdefault(
            ym,
            {
                "year_month": ym,
                "totals_by_currency": {},
                "total_czk": 0.0,
                "items": [],
            },
        )
        if has_amt:
            slot["totals_by_currency"][ccy] = (
                slot["totals_by_currency"].get(ccy, 0.0) + float(t)
            )
        if isinstance(czk, (int, float)):
            slot["total_czk"] += float(czk)
        slot["items"].append(
            {
                "id": row.get("id"),
                "invoice_number": row.get("invoice_number"),
                "client_name": row.get("client_name"),
                "amount": row.get("amount"),
                "currency": row.get("currency"),
                "amount_czk": row.get("amount_czk"),
                "cnb_valuation_date": row.get("cnb_valuation_date"),
                "invoice_date": row.get("invoice_date"),
                "payment_date": row.get("payment_date"),
            }
        )

    def _month_key(k: str) -> str:
        if k == "unknown":
            return "0000-00"
        return k

    months_list = sorted(months.values(), key=lambda x: _month_key(x["year_month"]), reverse=True)
    for m in months_list:
        m["totals_by_currency"] = {
            k: round(v, 2) for k, v in sorted(m["totals_by_currency"].items())
        }
        m["total_czk"] = round(m["total_czk"], 2)
        m["items"].sort(
            key=lambda x: (x.get("invoice_date") or "", x.get("invoice_number") or ""),
            reverse=True,
        )

    invoice_month_stems = sorted(
        (
            m["year_month"]
            for m in months_list
            if m.get("year_month") and m["year_month"] != "unknown"
        ),
        reverse=True,
    )

    inc_czk = round(grand_czk, 2)
    exp_czk = round(float(expenses_total_czk), 2)
    month_share: List[Dict[str, Any]] = []
    if inc_czk > 0:
        for m in months_list:
            tc = float(m["total_czk"])
            if tc <= 0:
                continue
            month_share.append(
                {
                    "year_month": m["year_month"],
                    "total_czk": m["total_czk"],
                    "pct_of_paid_total": round(100.0 * tc / float(inc_czk), 1),
                }
            )

    return {
        "file_count": len(rows),
        "grand_totals_by_currency_paid": {
            k: round(v, 2) for k, v in sorted(grand_ccy.items())
        },
        "income_grand_total_czk_paid": inc_czk,
        "income_fx_skipped": fx_skip,
        "pending_count": pending_count,
        "pending_totals_by_currency": {
            k: round(v, 2) for k, v in sorted(pending_ccy.items())
        },
        "months": months_list,
        "month_share_paid_czk": month_share,
        "expenses_total_czk": exp_czk,
        "net_czk": round(inc_czk - exp_czk, 2),
        "invoice_month_count": len(invoice_month_stems),
        "invoice_month_stems": invoice_month_stems,
    }


def build_overview(output_dir: Path) -> Dict[str, Any]:
    labels = _category_labels()
    months_out: List[Dict[str, Any]] = []
    grand_ccy: Dict[str, float] = {}
    grand_cat: Dict[str, Dict[str, float]] = {}
    grand_vat_ccy: Dict[str, float] = {}
    grand_czk_receipts = 0.0
    grand_vat_czk = 0.0
    fx_skip_receipts = 0
    fx_skip_vat_receipts = 0
    receipt_vat_by_rate: Dict[str, Dict[str, Any]] = {}

    paths = sorted(
        (
            p
            for p in list_month_data_files(output_dir)
            if p.name not in _OVERVIEW_SKIP_NAMES
        ),
        key=_month_sort_key,
        reverse=True,
    )
    month_bucket_paths = [p for p in paths if p.stem.lower() != "unknown"]
    total_receipts = 0

    for path in paths:
        data = load_json(path)
        recs = data.get("receipts") or []
        total_receipts += len(recs)
        ym = data.get("year_month") or path.stem

        receipt_rows: List[Dict[str, Any]] = []
        month_vat_ccy: Dict[str, float] = {}
        for r in recs:
            cat = r.get("category")
            t = r.get("total")
            ccy = r.get("currency")
            vat_amt = r.get("vat_amount")
            vdate = _receipt_valuation_date(r)
            amt_czk = None
            if isinstance(t, (int, float)) and ccy:
                amt_czk = _amount_in_czk(float(t), str(ccy), vdate)
            if isinstance(t, (int, float)) and ccy and str(ccy).strip().upper() not in (
                "",
                "UNK",
            ):
                if amt_czk is not None:
                    grand_czk_receipts += amt_czk
                else:
                    fx_skip_receipts += 1
            vat_czk = None
            cur_u = str(ccy).strip().upper() if ccy else ""
            if isinstance(vat_amt, (int, float)) and cur_u not in ("", "UNK"):
                month_vat_ccy[cur_u] = month_vat_ccy.get(cur_u, 0.0) + float(vat_amt)
                grand_vat_ccy[cur_u] = grand_vat_ccy.get(cur_u, 0.0) + float(vat_amt)
                vat_czk = _amount_in_czk(float(vat_amt), cur_u, vdate)
                if vat_czk is not None:
                    grand_vat_czk += vat_czk
                elif cur_u != "CZK":
                    fx_skip_vat_receipts += 1
                if vat_czk is not None:
                    vr0 = r.get("vat_rate")
                    if isinstance(vr0, (int, float)):
                        rf = float(vr0)
                        rk = (
                            str(int(rf))
                            if abs(rf - int(rf)) < 1e-6
                            else str(round(rf, 2))
                        )
                    else:
                        rk = "unknown"
                    br = receipt_vat_by_rate.setdefault(
                        rk,
                        {"receipt_count": 0, "vat_czk": 0.0},
                    )
                    br["receipt_count"] += 1
                    br["vat_czk"] = float(br["vat_czk"]) + float(vat_czk)
            receipt_rows.append(
                {
                    "id": r.get("id"),
                    "date": r.get("date"),
                    "payment_date": r.get("payment_date"),
                    "source_file": r.get("source_file"),
                    "total": r.get("total"),
                    "currency": ccy,
                    "amount_czk": amt_czk,
                    "vat_amount": r.get("vat_amount"),
                    "vat_rate": r.get("vat_rate"),
                    "vat_amount_czk": vat_czk,
                    "category": cat,
                    "category_label_cs": (
                        labels.get(cat, cat)
                        if cat
                        else "Nezařazeno"
                    ),
                    "merchant_hint": r.get("merchant_hint"),
                }
            )

        for ccy, v in (data.get("totals_by_currency") or {}).items():
            grand_ccy[ccy] = grand_ccy.get(ccy, 0.0) + float(v)

        for cat, cur_map in (data.get("totals_by_category") or {}).items():
            slot = grand_cat.setdefault(cat, {})
            for ccy, v in cur_map.items():
                slot[ccy] = slot.get(ccy, 0.0) + float(v)

        months_out.append(
            {
                "bucket": path.name,
                "year_month": ym,
                "totals_by_currency": {
                    k: round(float(v), 2)
                    for k, v in sorted(
                        (data.get("totals_by_currency") or {}).items()
                    )
                },
                "totals_by_category": {
                    ck: {
                        c: round(float(cv), 2)
                        for c, cv in sorted(cv.items())
                    }
                    for ck, cv in sorted(
                        (data.get("totals_by_category") or {}).items()
                    )
                },
                "vat_totals_by_currency": {
                    k: round(float(v), 2)
                    for k, v in sorted(month_vat_ccy.items())
                },
                "receipt_count": len(recs),
                "receipts": sorted(
                    receipt_rows,
                    key=lambda x: (x.get("date") or "", x.get("source_file") or ""),
                    reverse=True,
                ),
            }
        )

    cat_labels_out: Dict[str, str] = {}
    for ck in grand_cat:
        if ck == "_uncategorized":
            cat_labels_out[ck] = "Nezařazeno"
        else:
            cat_labels_out[ck] = labels.get(ck, ck)

    raw_trips = list_trips(output_dir)
    travel_block = _travel_for_overview(raw_trips)
    stravne_meta = load_stravne_meta()

    grand_czk_travel = 0.0
    fx_skip_trips = 0
    for trip in raw_trips:
        eff = effective_trip_amounts(trip)
        amt = eff["amount_total"]
        tccy = eff["currency"]
        if not isinstance(amt, (int, float)):
            continue
        vd_raw = trip.get("date_to") or trip.get("date_from")
        vd: Optional[str] = None
        if isinstance(vd_raw, str):
            s = vd_raw.strip()
            vd = s if _ISO_DATE.match(s) else None
        czk = _amount_in_czk(float(amt), str(tccy) if tccy else None, vd)
        if czk is not None:
            grand_czk_travel += czk
        elif (tccy or "").strip().upper() not in ("", "UNK"):
            fx_skip_trips += 1

    expenses_czk_combined = round(grand_czk_receipts + grand_czk_travel, 2)
    inc_block = aggregate_income_for_overview(
        list_income_rows(output_dir).get("rows") or [],
        expenses_czk_combined,
    )

    obl_sum = build_obligations_summary(output_dir)
    obl_unpaid_czk = float(obl_sum["unpaid"]["total_unpaid_czk"])
    net_after_obl = round(float(inc_block["net_czk"]) - obl_unpaid_czk, 2)

    receipt_month_stems = {p.stem for p in month_bucket_paths}
    period_stems_merged = receipt_month_stems | all_obligation_nav_months(output_dir)
    period_stems_merged.discard("unknown")
    period_bucket_stems = sorted(
        period_stems_merged, key=_month_stem_sort_key, reverse=True
    )

    vat_by_rate_out = {
        k: {
            "receipt_count": int(v["receipt_count"]),
            "vat_czk": round(float(v["vat_czk"]), 2),
        }
        for k, v in sorted(
            receipt_vat_by_rate.items(),
            key=lambda item: (item[0] == "unknown", item[0]),
        )
    }

    return {
        "total_receipts": total_receipts,
        "month_count": len(period_bucket_stems),
        "period_bucket_stems": period_bucket_stems,
        "grand_total_czk": round(grand_czk_receipts + grand_czk_travel, 2),
        "grand_total_czk_receipts_only": round(grand_czk_receipts, 2),
        "grand_total_czk_travel_only": round(grand_czk_travel, 2),
        "fx_skipped_receipts": fx_skip_receipts,
        "fx_skipped_trips": fx_skip_trips,
        "grand_totals_by_currency": {
            k: round(v, 2) for k, v in sorted(grand_ccy.items())
        },
        "grand_vat_totals_by_currency": {
            k: round(v, 2) for k, v in sorted(grand_vat_ccy.items())
        },
        "grand_vat_czk": round(grand_vat_czk, 2),
        "fx_skipped_vat_receipts": fx_skip_vat_receipts,
        "receipt_vat_by_rate": vat_by_rate_out,
        "grand_totals_by_category": {
            ck: {c: round(cv, 2) for c, cv in sorted(cv.items())}
            for ck, cv in sorted(grand_cat.items())
        },
        "category_labels_cs": cat_labels_out,
        "months": months_out,
        "travel_count": len(raw_trips),
        "travel_trips": travel_block["trips"],
        "travel_totals_by_currency": travel_block["totals_by_currency"],
        "travel_stravne_reference_year": stravne_meta.get("reference_year"),
        "travel_stravne_legal_note_cs": stravne_meta.get("legal_note_cs"),
        "income": inc_block,
        "obligations": {
            "paid_czk": obl_sum["paid"]["total_paid_czk"],
            "unpaid_czk": obl_unpaid_czk,
        },
        "net_czk_after_obligations": net_after_obl,
    }
