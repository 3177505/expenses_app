import html
import mimetypes
from datetime import date
from typing import Any, Dict, List, Optional

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dateutil.relativedelta import relativedelta
from pydantic import BaseModel

from expense_scanner.config import REPO_ROOT
from expense_scanner.tax.categories import load_tax_categories
from expense_scanner.tax.cz_stravne import (
    effective_trip_amounts,
    load_stravne_meta,
    suggest_foreign_meal_allowance,
)
from expense_scanner.store.invoice_store import (
    build_monthly_approx_summary,
    list_income_rows,
    patch_row,
    resolve_path_for_id,
    set_invoices_dir,
)
from expense_scanner.store.landing_notes import get_notes, set_notes
from expense_scanner.store.merchant_rules import apply_rules_to_uncategorized, list_rules_public
from expense_scanner.store.obligations_store import (
    add_entry as add_obl_entry,
    build_obligations_summary as build_obl_summary,
    delete_entry as delete_obl_entry,
    get_meta as get_obl_meta,
    list_entries as list_obl_entries,
    list_presets as list_obl_presets,
    persist_summary as persist_obl_summary,
    set_meta_fields as set_obl_meta_fields,
    update_entry as update_obl_entry,
)
from expense_scanner.tax.overview_data import build_overview, expense_czk_totals_by_month
from expense_scanner.ingest.vat_refresh import refresh_vat_from_source_files
from expense_scanner.ingest.pipeline import process_inbox
from expense_scanner.tax.reminder_schedule import build_reminder_overview
from expense_scanner.store.receipt_search import search_receipts
from expense_scanner.tax.tax_rc_review import build_tax_rc_review, set_dismissed_receipt_id
from expense_scanner.store.travel_store import (
    add_trip,
    delete_trip,
    list_trips,
    update_trip,
)
from expense_scanner.store.receipt_edit import (
    apply_receipt_update,
    find_duplicate_receipt_groups,
    find_receipt,
    list_incomplete_receipts,
    list_uncategorized_receipts,
    remove_receipts_by_ids,
    safe_inbox_file,
)
from app.i18n import (
    LANG_COOKIE,
    categories_i18n_json,
    dup_i18n_json,
    get_lang,
    html_lang_attr,
    income_i18n_json,
    incomplete_i18n_json,
    index_i18n_json,
    nav_html,
    next_path_from_request,
    normalize_lang,
    obligations_i18n_json,
    overview_i18n_json,
    reminders_i18n_json,
    search_i18n_json,
    tax_i18n_json,
    tax_rc_i18n_json,
    travel_i18n_json,
    tr,
)

ROOT = REPO_ROOT
OUTPUT = ROOT / "output"
OUTPUT.mkdir(parents=True, exist_ok=True)

NAV_HIGHLIGHT_SCRIPT = """<script>(function(){var p=location.pathname;if(p==="/"){var b=document.querySelector(".app-brand");if(b)b.classList.add("nav-current");return;}document.querySelectorAll(".app-nav-links a[href]").forEach(function(a){try{var u=new URL(a.href,location.origin);if(u.pathname===p)a.classList.add("nav-current");}catch(e){}});})();</script>"""

APP_SHELL_CSS = """<style>
:root {
  --bg: #161920;
  --surface: #1c2128;
  --surface-2: #222831;
  --card: #252b34;
  --text: #c5cad3;
  --muted: #7d8696;
  --border: #363d4a;
  --accent: #b5982f;
  --accent-hover: #c9ab45;
  --accent-soft: rgba(181, 152, 47, 0.11);
  --px: 1.25rem;
  --py-nav: 0.75rem;
  --py-main-top: 1.5rem;
  --py-main-bottom: 3rem;
  --gap: 1rem;
  --gap-sm: 0.5rem;
  --gap-xs: 0.35rem;
  --pad-card-y: 1.125rem;
  --pad-card-x: 1.25rem;
  --maxw: min(96vw, 100rem);
}
* { box-sizing: border-box; }
::selection { background: var(--accent-soft); color: var(--text); }
body {
  margin: 0;
  font-family: ui-monospace, "Cascadia Code", "SF Mono", Consolas, "Liberation Mono", monospace;
  font-size: 15px;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
}
a, a:visited { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hover); text-decoration: none; }
.app-nav { background: var(--surface); border-bottom: 1px solid var(--border); }
.app-nav-inner {
  max-width: var(--maxw);
  margin: 0 auto;
  padding: var(--py-nav) var(--px);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--gap) 1.75rem;
}
.app-brand {
  font-weight: 700;
  font-size: 1rem;
  color: var(--text);
  text-decoration: none;
  letter-spacing: 0.02em;
  line-height: 1.2;
  flex-shrink: 0;
}
.app-brand:hover { color: var(--accent-hover); text-decoration: none; }
.app-nav-groups {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--gap) 1.5rem;
  flex: 1;
  min-width: 0;
}
.app-nav-group {
  display: flex;
  flex-direction: column;
  gap: var(--gap-xs);
  min-width: 5.75rem;
}
.app-nav-group > span {
  font-size: 0.62rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--muted);
  font-weight: 600;
  line-height: 1;
}
.app-nav-links { display: flex; flex-wrap: wrap; align-items: center; gap: var(--gap-xs) 0.9rem; }
.app-nav a:not(.app-brand) {
  color: var(--muted);
  text-decoration: none;
  font-size: 0.88rem;
  font-weight: 500;
  line-height: 1.3;
}
.app-nav a:not(.app-brand):hover { color: var(--accent-hover); text-decoration: none; }
.app-nav a.nav-current:not(.app-brand) {
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
  padding: 0.15rem 0.4rem;
  margin: -0.15rem -0.4rem;
  background: var(--accent-soft);
  border-radius: 3px;
}
.app-brand.nav-current { color: var(--accent); text-decoration: none; }
.app-main {
  max-width: var(--maxw);
  margin: 0 auto;
  padding: var(--py-main-top) var(--px) var(--py-main-bottom);
}
.app-main > h1 {
  margin: 0 0 var(--gap-sm);
  font-size: 1.35rem;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: -0.02em;
}
.app-main > h1 + .lead { margin-top: 0; margin-bottom: var(--gap); }
.lead { margin: 0 0 var(--gap); color: var(--muted); font-size: 0.92rem; max-width: 50rem; }
.btn-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--gap-sm) var(--gap); margin-top: var(--gap-sm); }
.home-section {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: var(--pad-card-y) var(--pad-card-x);
  margin-bottom: var(--gap);
}
.home-section h2 {
  margin: 0 0 var(--gap-sm);
  font-size: 0.8rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
  line-height: 1.2;
}
.home-section p { margin: 0 0 var(--gap-sm); font-size: 0.9rem; color: var(--text); }
.home-section p:last-child { margin-bottom: 0; }
.home-section .btn-row { margin-top: var(--gap-sm); }
.home-section ul { margin: var(--gap-sm) 0 0; padding-left: 1.35rem; }
.home-section ul li { margin: 0.2rem 0; }
.form-block { margin-top: var(--gap-sm); }
.form-block label { display: block; margin-bottom: var(--gap-xs); font-weight: 600; font-size: 0.88rem; }
.form-block textarea, .form-block input[type="text"] { width: 100%; max-width: 42rem; }
.btn-primary {
  background: var(--surface-2);
  color: var(--accent);
  border: 1px solid var(--border);
  padding: 0.5rem 0.95rem;
  border-radius: 3px;
  font-size: 0.88rem;
  cursor: pointer;
  font-weight: 600;
  font-family: inherit;
  line-height: 1.2;
}
.btn-primary:hover { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-hover); }
.btn-secondary {
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--border);
  padding: 0.5rem 0.95rem;
  border-radius: 3px;
  font-size: 0.88rem;
  cursor: pointer;
  font-weight: 500;
  font-family: inherit;
  line-height: 1.2;
}
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); }
.page-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: var(--pad-card-y) var(--pad-card-x);
  margin-bottom: var(--gap);
}
.page-card h1 { margin: 0 0 var(--gap-sm); font-size: 1.35rem; font-weight: 700; line-height: 1.25; letter-spacing: -0.02em; }
.page-card h2 { margin: 0 0 var(--gap-sm); font-size: 1rem; line-height: 1.3; }
.page-card section { margin-bottom: var(--gap); }
.page-card section:last-child { margin-bottom: 0; }
.page-card .btn-primary { margin-top: var(--gap-sm); }
input, select, textarea {
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0.45rem 0.6rem;
  background: var(--surface);
  color: var(--text);
  font-family: inherit;
  font-size: 0.9rem;
  line-height: 1.35;
}
code {
  background: var(--surface);
  padding: 0.1rem 0.32rem;
  border-radius: 3px;
  font-size: 0.86em;
  border: 1px solid var(--border);
  color: var(--accent-hover);
}
.app-nav-lang {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-left: auto;
  flex-shrink: 0;
}
.app-nav-lang a { color: var(--muted) !important; font-size: 0.88rem; }
.app-nav-lang a.lang-active { color: var(--accent) !important; font-weight: 600; }
.lang-sep { color: var(--muted); user-select: none; }
</style>"""

app = FastAPI()
app.mount("/data", StaticFiles(directory=str(OUTPUT)), name="data")


def _np(request: Request) -> str:
    return next_path_from_request(request)


@app.get("/set-lang/{code}")
def set_lang(code: str, nxt: str = Query("/", alias="next")):
    lg = normalize_lang(code)
    dest = nxt if isinstance(nxt, str) and nxt.startswith("/") else "/"
    r = RedirectResponse(url=dest, status_code=302)
    r.set_cookie(
        LANG_COOKIE,
        lg,
        max_age=365 * 24 * 3600,
        path="/",
        samesite="lax",
    )
    return r


class RefreshVatBody(BaseModel):
    force: bool = False


class ReceiptPatchBody(BaseModel):
    date: Optional[str] = None
    payment_date: Optional[str] = None
    total: Optional[float] = None
    currency: Optional[str] = None
    vat_amount: Optional[float] = None
    vat_rate: Optional[float] = None
    merchant_hint: Optional[str] = None
    category: Optional[str] = None
    category_note: Optional[str] = None
    remember_for_merchant: Optional[bool] = None


class DuplicateRemoveBody(BaseModel):
    remove_ids: List[str]


class TaxRcDismissBody(BaseModel):
    receipt_id: str
    dismissed: bool = True


class TripCreateBody(BaseModel):
    purpose: str = ""
    country_code: str = ""
    destination: str = ""
    date_from: str
    date_to: str
    claim_type: str = "meal_allowance_cz"
    amount_total: Optional[float] = None
    currency: str = "CZK"
    notes: Optional[str] = None


class TripPatchBody(BaseModel):
    purpose: Optional[str] = None
    country_code: Optional[str] = None
    destination: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    claim_type: Optional[str] = None
    amount_total: Optional[float] = None
    currency: Optional[str] = None
    notes: Optional[str] = None


class LandingNotesBody(BaseModel):
    text: str = ""


class ObligationCreateBody(BaseModel):
    kind: str = "other"
    title: Optional[str] = None
    amount: float
    currency: str = "CZK"
    due_date: str
    paid_date: Optional[str] = None
    period_month: Optional[str] = None
    notes: Optional[str] = None


class ObligationsMetaBody(BaseModel):
    osvc_since: Optional[str] = None
    sickness_from: Optional[str] = None
    vat_identified: Optional[bool] = None
    vat_identified_from: Optional[str] = None


class IncomeDirBody(BaseModel):
    invoices_dir: str


class IncomeRowPatchBody(BaseModel):
    paid: bool = False
    paid_month: Optional[str] = None
    payment_date: Optional[str] = None
    client_dic: Optional[str] = None
    client_vat: Optional[str] = None
    in_approx_selected: bool = False


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    lang = get_lang(request)
    np = _np(request)
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ix = index_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("index.title"))
        + """</title>
    """
        + APP_SHELL_CSS
        + """
    <style>
      #months { margin: 0; padding-left: 1.35rem; }
      #months li { margin: 0.2rem 0; }
      #status { color: var(--muted); font-size: 0.88rem; vertical-align: middle; }
    </style>
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main">
      <h1>"""
        + html.escape(L("index.h1"))
        + """</h1>
      <p class="lead">"""
        + L("index.lead")
        + """</p>
      <div class="home-section">
        <h2>"""
        + html.escape(L("index.inbox_h"))
        + """</h2>
        <p>"""
        + html.escape(L("index.inbox_p"))
        + """</p>
        <div class="btn-row"><button type="button" class="btn-primary" id="run">"""
        + html.escape(L("index.btn_inbox"))
        + """</button><span id="status"></span></div>
      </div>
      <div class="home-section">
        <h2>"""
        + L("index.data_h")
        + """</h2>
        <p>"""
        + L("index.data_p")
        + """</p>
        <ul id="months"></ul>
      </div>
      <div class="home-section">
        <h2>"""
        + html.escape(L("index.notes_h"))
        + """</h2>
        <p>"""
        + L("index.notes_p")
        + """</p>
        <div class="form-block">
          <label for="my_notes">"""
        + html.escape(L("index.notes_lbl"))
        + """</label>
          <textarea id="my_notes" rows="6" placeholder=\""""
        + html.escape(L("index.notes_ph"))
        + """\"></textarea>
        </div>
        <div class="btn-row"><button type="button" class="btn-primary" id="save_notes">"""
        + html.escape(L("index.btn_notes"))
        + """</button><span id="notes_status"></span></div>
      </div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const IX = """
        + ix
        + """;
      async function refreshMonths() {
        const ul = document.getElementById("months");
        ul.innerHTML = "";
        const base = "/data/";
        const r = await fetch("/api/output-month-files");
        const names = r.ok ? await r.json() : [];
        for (const name of names) {
          const li = document.createElement("li");
          const a = document.createElement("a");
          a.href = base + name;
          a.textContent = name;
          li.appendChild(a);
          ul.appendChild(li);
        }
      }
      document.getElementById("run").onclick = async () => {
        const s = document.getElementById("status");
        s.textContent = "…";
        const r = await fetch("/api/process", { method: "POST" });
        const j = await r.json();
        s.textContent = JSON.stringify(j);
        await refreshMonths();
      };
      async function loadNotes() {
        const r = await fetch("/api/landing-notes");
        const j = await r.json();
        document.getElementById("my_notes").value = j.text || "";
      }
      document.getElementById("save_notes").onclick = async () => {
        const st = document.getElementById("notes_status");
        st.textContent = "…";
        const r = await fetch("/api/landing-notes", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: document.getElementById("my_notes").value })
        });
        const j = await r.json().catch(() => ({}));
        st.textContent = r.ok ? (IX.saved + (j.updated_at ? " · " + j.updated_at : "")) : (IX.err + " " + r.status);
      };
      refreshMonths();
      loadNotes();
    </script>
  </body>
</html>
"""
    )


def incomplete_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ii = incomplete_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("inc.title"))
        + """</title>
    """
        + APP_SHELL_CSS
        + """
    <style>
      .thumb { max-width: 100%; max-height: 22rem; border-radius: 4px; border: 1px solid var(--border); }
      .page-card form > div { margin-top: var(--gap-sm); }
      .page-card form > div:first-child { margin-top: 0; }
    </style>
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main">
      <h1>"""
        + html.escape(L("inc.h1"))
        + """</h1>
      <p class="lead">"""
        + html.escape(L("inc.lead"))
        + """</p>
      <p id="empty" class="lead" hidden>"""
        + html.escape(L("inc.empty"))
        + """</p>
      <div id="list"></div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const II = """
        + ii
        + """;
      function fieldRow(label, key, type, value, placeholder) {
        const id = key + "-" + Math.random().toString(36).slice(2);
        const wrap = document.createElement("div");
        const lab = document.createElement("label");
        lab.textContent = label + " ";
        lab.htmlFor = id;
        let el;
        if (type === "date") {
          el = document.createElement("input");
          el.type = "date";
          el.id = id;
          el.value = value || "";
        } else if (type === "number") {
          el = document.createElement("input");
          el.type = "number";
          el.step = "any";
          el.id = id;
          el.value = value != null && value !== "" ? value : "";
        } else {
          el = document.createElement("input");
          el.type = "text";
          el.id = id;
          el.value = value || "";
          el.placeholder = placeholder || "";
        }
        wrap.appendChild(lab);
        wrap.appendChild(el);
        wrap.dataset.field = key;
        wrap._input = el;
        return wrap;
      }

      async function loadList() {
        const r = await fetch("/api/incomplete");
        const items = await r.json();
        const list = document.getElementById("list");
        const empty = document.getElementById("empty");
        list.innerHTML = "";
        if (!items.length) {
          empty.hidden = false;
          return;
        }
        empty.hidden = true;
        for (const item of items) {
          const rec = item.receipt;
          const card = document.createElement("section");
          card.className = "page-card";
          const h = document.createElement("h2");
          h.textContent = rec.source_file || item.id;
          card.appendChild(h);
          const miss = document.createElement("p");
          miss.textContent = II.missing + " " + item.missing.join(", ");
          card.appendChild(miss);
          const prev = document.createElement("p");
          const img = document.createElement("img");
          img.className = "thumb";
          img.alt = II.receiptAlt;
          img.src = "/api/receipts/" + encodeURIComponent(item.id) + "/file";
          img.onerror = () => { img.remove(); };
          prev.appendChild(img);
          card.appendChild(prev);
          const form = document.createElement("div");
          const fd = fieldRow(II.lblDate, "date", "date", rec.date, "");
          const fpay = fieldRow(II.lblPaymentDate, "payment_date", "date", rec.payment_date, "");
          const ft = fieldRow(II.lblTotal, "total", "number", rec.total, "");
          const fv = fieldRow(II.lblVat, "vat_amount", "number", rec.vat_amount, "");
          const fc = fieldRow(II.lblCcy, "currency", "text", rec.currency, "CZK");
          form.appendChild(fd);
          form.appendChild(fpay);
          form.appendChild(ft);
          form.appendChild(fv);
          form.appendChild(fc);
          card.appendChild(form);
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn-primary";
          btn.textContent = II.save;
          btn.onclick = async () => {
            const body = {
              date: fd._input.value ? fd._input.value : null,
              payment_date: fpay._input.value ? fpay._input.value : null,
              total: ft._input.value === "" ? null : Number(ft._input.value),
              vat_amount: fv._input.value === "" ? null : Number(fv._input.value),
              currency: fc._input.value.trim() || null
            };
            const res = await fetch("/api/receipts/" + encodeURIComponent(item.id), {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body)
            });
            if (!res.ok) {
              const err = await res.text();
              alert(II.alertFail + " " + res.status + " " + err);
              return;
            }
            card.remove();
            loadList();
          };
          card.appendChild(btn);
          list.appendChild(card);
        }
      }
      loadList();
    </script>
  </body>
</html>
"""
)


def categories_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ci = categories_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("cat.title"))
        + """</title>
    """
        + APP_SHELL_CSS
        + """
    <style>
      .thumb { max-width: 100%; max-height: 22rem; border-radius: 4px; border: 1px solid var(--border); }
    </style>
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main">
      <h1>"""
        + html.escape(L("cat.h1"))
        + """</h1>
      <p class="lead">"""
        + L("cat.lead")
        + """</p>
      <p id="empty" class="lead" hidden>"""
        + html.escape(L("cat.empty"))
        + """</p>
      <div id="list"></div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const CI = """
        + ci
        + """;
      async function loadCats() {
        const r = await fetch("/api/tax-categories");
        return r.json();
      }

      async function loadList() {
        const bundle = await loadCats();
        const cats = bundle.categories || [];
        const r = await fetch("/api/uncategorized");
        const items = await r.json();
        const list = document.getElementById("list");
        const empty = document.getElementById("empty");
        list.innerHTML = "";
        if (!items.length) {
          empty.hidden = false;
          return;
        }
        empty.hidden = true;
        for (const item of items) {
          const rec = item.receipt;
          const card = document.createElement("section");
          card.className = "page-card";
          const h = document.createElement("h2");
          h.textContent = rec.source_file || item.id;
          card.appendChild(h);
          const meta = document.createElement("p");
          meta.textContent = [
            rec.date || CI.noDate,
            rec.total != null ? rec.total : "",
            rec.vat_amount != null ? rec.vat_amount + " " + CI.vatAbbr : "",
            rec.currency || "",
            rec.merchant_hint || ""
          ].filter(Boolean).join(" · ");
          card.appendChild(meta);
          const prev = document.createElement("p");
          const img = document.createElement("img");
          img.className = "thumb";
          img.alt = CI.receiptAlt;
          img.src = "/api/receipts/" + encodeURIComponent(item.id) + "/file";
          img.onerror = () => { img.remove(); };
          prev.appendChild(img);
          card.appendChild(prev);
          const selLab = document.createElement("label");
          selLab.textContent = CI.lblCat + " ";
          const sel = document.createElement("select");
          const opt0 = document.createElement("option");
          opt0.value = "";
          opt0.textContent = CI.choose;
          sel.appendChild(opt0);
          for (const c of cats) {
            const o = document.createElement("option");
            o.value = c.id;
            o.textContent = c.label_cs;
            sel.appendChild(o);
          }
          selLab.appendChild(sel);
          card.appendChild(selLab);
          const noteLab = document.createElement("label");
          noteLab.textContent = CI.note;
          const note = document.createElement("textarea");
          note.rows = 2;
          note.style.width = "100%";
          note.style.maxWidth = "36rem";
          note.value = rec.category_note || "";
          noteLab.appendChild(document.createElement("br"));
          noteLab.appendChild(note);
          card.appendChild(noteLab);
          const payLab = document.createElement("label");
          payLab.textContent = CI.lblPaymentDate + " ";
          const payIn = document.createElement("input");
          payIn.type = "date";
          payIn.value = (rec.payment_date && String(rec.payment_date).slice(0, 10)) || "";
          payLab.appendChild(payIn);
          card.appendChild(payLab);
          const rememberWrap = document.createElement("label");
          const rememberCb = document.createElement("input");
          rememberCb.type = "checkbox";
          rememberCb.checked = false;
          rememberCb.disabled = !(rec.merchant_hint && String(rec.merchant_hint).trim());
          rememberWrap.appendChild(rememberCb);
          rememberWrap.appendChild(document.createTextNode(" " + CI.rememberMerchant));
          card.appendChild(rememberWrap);
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn-primary";
          btn.textContent = CI.save;
          btn.onclick = async () => {
            if (!sel.value) {
              alert(CI.alertPick);
              return;
            }
            const body = {
              category: sel.value,
              category_note: note.value.trim() || null,
              payment_date: payIn.value ? payIn.value : null
            };
            if (rememberCb.checked) body.remember_for_merchant = true;
            const res = await fetch("/api/receipts/" + encodeURIComponent(item.id), {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body)
            });
            if (!res.ok) {
              const err = await res.text();
              alert(CI.alertFail + " " + res.status + " " + err);
              return;
            }
            card.remove();
            loadList();
          };
          card.appendChild(btn);
          list.appendChild(card);
        }
      }
      loadList();
    </script>
  </body>
</html>
"""
)


def tax_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    tx = tax_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("tax.title"))
        + """</title>
    """
        + APP_SHELL_CSS
        + """
    <style>
      #root ul { margin: var(--gap-sm) 0 0; padding-left: 1.35rem; }
      #root li { margin: 0.65rem 0; }
      #root .ref { color: var(--muted); }
    </style>
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main">
      <div id="root" class="page-card"></div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const TX = """
        + tx
        + """;
      (async () => {
        const r = await fetch("/api/tax-categories");
        const b = await r.json();
        const root = document.getElementById("root");
        const h1 = document.createElement("h1");
        h1.textContent = TX.h1;
        root.appendChild(h1);
        const leg = document.createElement("p");
        leg.className = "lead";
        leg.textContent = b.legal_framework_cs || "";
        root.appendChild(leg);
        const disc = document.createElement("p");
        disc.className = "lead";
        disc.textContent = b.disclaimer_cs || "";
        root.appendChild(disc);
        const ul = document.createElement("ul");
        for (const c of (b.categories || [])) {
          const li = document.createElement("li");
          const strong = document.createElement("strong");
          strong.textContent = c.id + ": " + c.label_cs;
          li.appendChild(strong);
          if (c.zdp_ref_cs) {
            li.appendChild(document.createTextNode(" "));
            const sp = document.createElement("span");
            sp.className = "ref";
            sp.textContent = "(" + c.zdp_ref_cs + ")";
            li.appendChild(sp);
          }
          if (c.examples_cs) {
            const br = document.createElement("br");
            li.appendChild(br);
            const ex = document.createElement("small");
            ex.style.color = "var(--muted)";
            ex.textContent = c.examples_cs;
            li.appendChild(ex);
          }
          ul.appendChild(li);
        }
        root.appendChild(ul);
      })();
    </script>
  </body>
</html>
"""
)


def tax_rc_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    rj = tax_rc_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("rc.title"))
        + """</title>
    """
        + APP_SHELL_CSS
        + """
    <style>
      .travel-main table { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: var(--gap); background: var(--card); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
      .travel-main th, .travel-main td { text-align: left; padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); vertical-align: top; }
      .travel-main th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; background: var(--surface); }
    </style>
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main travel-main">
      <h1>"""
        + html.escape(L("rc.h1"))
        + """</h1>
      <p class="lead">"""
        + L("rc.lead")
        + """</p>
      <p id="rc_status" class="lead"></p>
      <div id="rc_table"><p class="hint">…</p></div>
      <div id="rc_dismissed_wrap" hidden>
        <h2 id="rc_hidden_h2"></h2>
        <div id="rc_dismissed"></div>
      </div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const RC = """
        + rj
        + """;
    function esc(s) {
      if (s == null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }
    function escAttr(s) {
      if (s == null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
    }
    function buildRcTableBody(rows, isHidden) {
      let h = "<table><thead><tr>";
      h += "<th>" + esc(RC.thDate) + "</th><th>" + esc(RC.thMerch) + "</th><th>" + esc(RC.thAmt) + "</th>";
      h += "<th>" + esc(RC.thVat) + "</th><th>" + esc(RC.thKw) + "</th><th>" + esc(RC.thBucket) + "</th>";
      h += "<th>" + esc(RC.thAction) + "</th><th></th></tr></thead><tbody>";
      for (const row of rows) {
        const rid = row.id;
        const amt = (row.total != null && row.currency) ? (esc(row.currency) + " " + esc(String(row.total))) : "—";
        const va = (row.vat_amount != null) ? esc(String(row.vat_amount)) : "—";
        const vr = (row.vat_rate != null) ? esc(String(row.vat_rate)) + "%" : "—";
        const kw = (row.pdf_keywords_matched && row.pdf_keywords_matched.length) ? esc(row.pdf_keywords_matched.join(", ")) : "—";
        h += "<tr><td>" + esc(row.date || "—") + "</td><td>" + esc(row.merchant_hint || "—") + "</td>";
        h += "<td>" + amt + "</td><td>" + va + " / " + vr + "</td>";
        h += "<td><small>" + kw + "</small></td><td>" + esc(row.bucket_file || "") + "</td>";
        if (isHidden) {
          h += "<td><button type=\\"button\\" class=\\"btn-secondary\\" data-rc-restore=\\"" + escAttr(rid) + "\\">" + esc(RC.restore) + "</button></td>";
        } else {
          h += "<td><button type=\\"button\\" class=\\"btn-secondary\\" data-rc-dismiss=\\"" + escAttr(rid) + "\\">" + esc(RC.dismiss) + "</button></td>";
        }
        h += "<td><a href=\\"/api/receipts/" + encodeURIComponent(rid) + "/file\\" target=\\"_blank\\">" + esc(RC.open) + "</a></td></tr>";
      }
      h += "</tbody></table>";
      return h;
    }
    const rcMain = document.querySelector(".travel-main");
    if (rcMain && !rcMain.dataset.rcWired) {
      rcMain.dataset.rcWired = "1";
      rcMain.addEventListener("click", function(ev) {
        const t = ev.target;
        if (!t || !t.getAttribute) return;
        const d1 = t.getAttribute("data-rc-dismiss");
        if (d1) { ev.preventDefault(); doRcDismiss(d1, true); return; }
        const d2 = t.getAttribute("data-rc-restore");
        if (d2) { ev.preventDefault(); doRcDismiss(d2, false); }
      });
    }
    async function doRcDismiss(receiptId, dismissed) {
      const st = document.getElementById("rc_status");
      st.textContent = "";
      const r = await fetch("/api/tax-rc-review/dismiss", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ receipt_id: receiptId, dismissed: dismissed })
      });
      if (!r.ok) { st.textContent = RC.loadErr; return; }
      await loadRc();
    }
    async function loadRc() {
      const box = document.getElementById("rc_table");
      const dbox = document.getElementById("rc_dismissed");
      const dwrap = document.getElementById("rc_dismissed_wrap");
      const h2el = document.getElementById("rc_hidden_h2");
      const st = document.getElementById("rc_status");
      st.textContent = "";
      box.innerHTML = "<p class=\\"hint\\">…</p>";
      dbox.innerHTML = "";
      dwrap.hidden = true;
      const r = await fetch("/api/tax-rc-review");
      if (!r.ok) { box.innerHTML = "<p class=\\"hint\\">" + esc(RC.loadErr) + "</p>"; return; }
      const j = await r.json();
      const rows = j.rows || [];
      const dis = j.dismissed_rows || [];
      if (!rows.length && !dis.length) { box.innerHTML = "<p class=\\"hint\\">" + esc(RC.empty) + "</p>"; return; }
      if (rows.length) {
        box.innerHTML = buildRcTableBody(rows, false);
      } else {
        box.innerHTML = "<p class=\\"hint\\">" + esc(RC.emptyVisible) + "</p>";
      }
      if (dis.length) {
        dwrap.hidden = false;
        h2el.textContent = RC.hiddenH2;
        dbox.innerHTML = buildRcTableBody(dis, true);
      }
    }
    loadRc();
    </script>
  </body>
</html>
"""
    )


def overview_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ovi = overview_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>"""
        + html.escape(L("ov.title"))
        + """</title>
  """
        + APP_SHELL_CSS
        + """
  <style>
    .overview-header { margin-bottom: var(--gap); }
    .overview-header h1 {
      margin: 0 0 var(--gap-xs);
      font-size: 1.35rem;
      font-weight: 700;
      line-height: 1.25;
      letter-spacing: -0.02em;
    }
    .overview-header p { margin: 0; color: var(--muted); font-size: 0.92rem; max-width: 42rem; }
    .overview-header .toolbar { margin-top: var(--gap-sm); display: flex; flex-wrap: wrap; gap: var(--gap-sm); align-items: center; }
    #overview-app > *:first-child { margin-top: 0; }
    #overview-app { width: 100%; min-width: 0; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: var(--gap-sm);
      margin-bottom: var(--gap);
      align-items: start;
    }
    .stat {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: var(--pad-card-y) var(--pad-card-x);
      box-sizing: border-box;
      width: 100%;
      max-width: 100%;
      aspect-ratio: 1 / 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      justify-content: center;
      overflow: auto;
    }
    .income-flow-block {
      margin: calc(-1 * var(--gap-sm)) 0 var(--gap);
      width: 100%;
      min-width: 0;
    }
    .income-flow-label {
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: var(--gap-xs);
      line-height: 1.2;
    }
    .income-flow-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      align-items: center;
      line-height: 1.35;
    }
    .panel-details.ov-income-details { margin: 0; }
    .panel-details.ov-income-details > summary {
      cursor: pointer;
      list-style: none;
      font-size: 0.82rem;
      margin: 0;
      padding: 0.65rem var(--pad-card-x);
      background: var(--accent-soft);
      border-bottom: 1px solid var(--border);
      font-weight: 650;
      line-height: 1.35;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: var(--gap-sm);
    }
    .panel-details.ov-income-details > summary::-webkit-details-marker { display: none; }
    .panel-details.ov-income-details > summary::before {
      content: "▸";
      margin-right: var(--gap-sm);
      color: var(--muted);
      font-weight: 400;
    }
    .panel-details.ov-income-details[open] > summary::before { content: "▾"; flex-shrink: 0; }
    .panel-details.ov-income-details .ov-income-sum-title { flex: 1; min-width: 0; font-weight: inherit; }
    .panel-details.ov-income-details .body { padding: var(--gap) var(--pad-card-x); border-top: 0; }
    .stat .label {
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      line-height: 1.2;
    }
    .stat .value { font-size: 1.28rem; font-weight: 650; margin-top: var(--gap-xs); line-height: 1.2; }
    .stat .stat-badges {
      margin-top: 0.4rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      align-items: center;
      line-height: 1.35;
    }
    .stat .stat-note {
      margin-top: 0.35rem;
      font-size: 0.65rem;
      color: var(--muted);
      text-transform: none;
      letter-spacing: 0;
      line-height: 1.25;
    }
    .ov-period-stems {
      font-size: 0.72rem;
      line-height: 1.35;
      font-weight: 400;
      text-transform: none;
      letter-spacing: 0.02em;
    }
    .panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 4px;
      margin-bottom: var(--gap);
      overflow: hidden;
    }
    .panel h2 {
      font-size: 0.82rem;
      margin: 0;
      padding: 0.65rem var(--pad-card-x);
      background: var(--accent-soft);
      border-bottom: 1px solid var(--border);
      font-weight: 650;
      line-height: 1.35;
    }
    .panel .body { padding: var(--gap) var(--pad-card-x); }
    .panel .body > *:first-child { margin-top: 0; }
    .panel .body > *:last-child { margin-bottom: 0; }
    #overview-app table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    #overview-app .panel,
    #overview-app details.month { min-width: 0; }
    #overview-app th, #overview-app td {
      text-align: left;
      padding: 0.5rem 0.65rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }
    #overview-app th {
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    #overview-app tbody tr:last-child td { border-bottom: none; }
    #overview-app tbody tr:hover { background: var(--surface-2); }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    details.month { margin-bottom: var(--gap-sm); border: 1px solid var(--border); border-radius: 4px; background: var(--card); }
    details.month summary {
      cursor: pointer;
      padding: 0.8rem var(--pad-card-x);
      font-weight: 650;
      list-style: none;
      display: flex;
      flex-wrap: wrap;
      gap: var(--gap-xs) var(--gap);
      align-items: baseline;
    }
    details.month summary::-webkit-details-marker { display: none; }
    details.month summary::before { content: "▸"; margin-right: var(--gap-sm); color: var(--muted); font-weight: 400; }
    details.month[open] summary::before { content: "▾"; }
    .month-meta { font-weight: 400; color: var(--muted); font-size: 0.88rem; }
    .month-meta-badges { display: inline-flex; flex-wrap: wrap; gap: 0.35rem; align-items: center; vertical-align: middle; }
    .month-inner { padding: var(--gap) var(--pad-card-x) var(--pad-card-y); border-top: 1px solid var(--border); min-width: 0; }
    .month-inner .month-h3 {
      font-size: 0.78rem;
      margin: var(--gap) 0 var(--gap-sm);
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .month-inner .month-h3:first-child { margin-top: 0; }
    .month-inner .month-h3 + table { margin-bottom: var(--gap); }
    .month-inner > table:last-child { margin-bottom: 0; }
    .badge {
      display: inline-block;
      background: var(--accent-soft);
      color: var(--accent);
      padding: 0.2rem 0.5rem;
      border-radius: 3px;
      font-size: 0.78rem;
      font-weight: 600;
      white-space: nowrap;
    }
    .empty { color: var(--muted); padding: var(--gap); text-align: center; margin: 0; }
    a.file-link { color: var(--accent); font-size: 0.86rem; }
    .ov-rcpt-scroll { overflow-x: auto; max-width: 100%; min-width: 0; }
    #overview-app .ov-rcpt-table {
      width: 100%;
      min-width: 0;
      table-layout: fixed;
    }
    #overview-app .ov-rcpt-table th:nth-child(1),
    #overview-app .ov-rcpt-table td:nth-child(1) { width: 6%; }
    #overview-app .ov-rcpt-table th:nth-child(2),
    #overview-app .ov-rcpt-table td:nth-child(2) { width: 11%; word-break: break-word; overflow-wrap: anywhere; }
    #overview-app .ov-rcpt-table th:nth-child(3),
    #overview-app .ov-rcpt-table td:nth-child(3) { width: 14%; min-width: 0; }
    #overview-app .ov-rcpt-table th:nth-child(4),
    #overview-app .ov-rcpt-table td:nth-child(4) { width: 12%; min-width: 0; word-break: break-word; overflow-wrap: anywhere; }
    #overview-app .ov-rcpt-table th:nth-child(5),
    #overview-app .ov-rcpt-table td:nth-child(5) { width: 10%; min-width: 0; }
    #overview-app .ov-rcpt-table th:nth-child(6),
    #overview-app .ov-rcpt-table td:nth-child(6) { width: 5%; min-width: 0; }
    #overview-app .ov-rcpt-table th:nth-child(7),
    #overview-app .ov-rcpt-table td:nth-child(7) { width: 9%; min-width: 0; }
    #overview-app .ov-rcpt-table th:nth-child(8),
    #overview-app .ov-rcpt-table td:nth-child(8) { width: 9%; min-width: 0; }
    #overview-app .ov-rcpt-table th:nth-child(9),
    #overview-app .ov-rcpt-table td:nth-child(9) { width: 9%; min-width: 0; }
    #overview-app .ov-rcpt-table th:nth-child(10),
    #overview-app .ov-rcpt-table td:nth-child(10) { width: 5%; min-width: 0; }
    #overview-app .ov-merch-input { min-width: 0; width: 100%; max-width: 100%; box-sizing: border-box; }
    #overview-app .ov-rcpt-table input.ov-rcpt-patch { box-sizing: border-box; max-width: 100%; min-width: 0; width: 100%; }
    button.ov-rcpt-open.file-link {
      background: none;
      border: none;
      padding: 0;
      cursor: pointer;
      font: inherit;
      font-size: 0.86rem;
      color: var(--accent);
      text-decoration: none;
    }
    button.ov-rcpt-open.file-link:hover { color: var(--accent-hover); }
    dialog.ov-rcpt-dlg {
      max-width: 96vw;
      width: min(96vw, 72rem);
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--card);
      color: var(--text);
      padding: 0;
    }
    dialog.ov-rcpt-dlg::backdrop { background: rgba(0, 0, 0, 0.6); }
    .ov-rcpt-dlg-box { padding: var(--pad-card-y) var(--pad-card-x); max-height: 90vh; overflow: auto; }
    .ov-rcpt-dlg-bar { margin-bottom: var(--gap-sm); text-align: right; }
    #ov-rcpt-dlg-img {
      display: block;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
    }
    #ov-rcpt-dlg-frame { width: 100%; min-height: 80vh; border: 0; background: var(--surface); }
    body.ov-privacy-blur #overview-app .stat .value,
    body.ov-privacy-blur #overview-app .stat .stat-badges,
    body.ov-privacy-blur #overview-app .stat .stat-note,
    body.ov-privacy-blur #overview-app .stat .ov-period-stems,
    body.ov-privacy-blur #overview-app .num,
    body.ov-privacy-blur #overview-app .badge,
    body.ov-privacy-blur #overview-app .income-flow-badges,
    body.ov-privacy-blur #overview-app input.ov-rcpt-patch,
    body.ov-privacy-blur #overview-app .month-meta-badges,
    body.ov-privacy-blur #overview-app details.month > summary,
    body.ov-privacy-blur #overview-app .month-meta:not(.month-meta-badges) {
      filter: blur(0.35rem);
      user-select: none;
    }
    body.ov-privacy-blur dialog.ov-rcpt-dlg #ov-rcpt-dlg-img,
    body.ov-privacy-blur dialog.ov-rcpt-dlg #ov-rcpt-dlg-frame {
      filter: blur(0.35rem);
    }
  </style>
</head>
<body>
  """
        + nav_html(lang, np)
        + """
  <main class="app-main">
    <header class="overview-header">
      <h1>"""
        + html.escape(L("ov.h1"))
        + """</h1>
      <p>"""
        + L("ov.lead")
        + """</p>
      <div class="toolbar"><button type="button" class="btn-primary" id="reload">"""
        + html.escape(L("ov.reload"))
        + """</button>
      <button type="button" class="btn-secondary" id="ov-refresh-vat">"""
        + html.escape(L("ov.reload_vat"))
        + """</button>
      <button type="button" class="btn-secondary" id="ov-privacy-toggle" aria-pressed="false" title='"""
        + html.escape(L("ov.privacy_title"))
        + """'>"""
        + html.escape(L("ov.privacy_hide"))
        + """</button></div>
    </header>
    <div id="overview-app"><p class="empty">"""
        + html.escape(L("ov.loading"))
        + """</p></div>
  </main>
  <dialog id="ov-rcpt-dlg" class="ov-rcpt-dlg">
    <div class="ov-rcpt-dlg-box">
      <div class="ov-rcpt-dlg-bar">
        <button type="button" class="btn-primary" id="ov-rcpt-dlg-close">"""
        + html.escape(L("ov.preview_close"))
        + """</button>
      </div>
      <img id="ov-rcpt-dlg-img" alt="" hidden />
      <iframe id="ov-rcpt-dlg-frame" title="preview" hidden></iframe>
    </div>
  </dialog>
  """
        + NAV_HIGHLIGHT_SCRIPT
        + """
  <script>
    const OVI = """
        + ovi
        + """;
    const nf = new Intl.NumberFormat(OVI.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    function money(n) { return n == null || n === "" ? "—" : nf.format(Number(n)); }

    function renderCatTable(totalsByCat, labelMap) {
      const keys = Object.keys(totalsByCat || {});
      if (!keys.length) return "<p class=\\"empty\\">" + escapeHtml(OVI.catEmpty) + "</p>";
      let rows = "";
      for (const ck of keys.sort()) {
        const cur = totalsByCat[ck];
        const parts = Object.keys(cur).sort().map(c => c + " " + money(cur[c])).join(", ");
        const title = labelMap[ck] || ck;
        rows += "<tr><td>" + escapeHtml(title) + "</td><td class=\\"num\\">" + escapeHtml(parts) + "</td></tr>";
      }
      return "<table><thead><tr><th>" + escapeHtml(OVI.thCat) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thAmounts) + "</th></tr></thead><tbody>" + rows + "</tbody></table>";
    }

    function escapeHtml(s) {
      if (s == null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    function renderGrandCcy(obj) {
      const parts = Object.keys(obj || {}).sort().map(k => "<span class=\\"badge\\">" + escapeHtml(k) + " " + money(obj[k]) + "</span>");
      return parts.length ? parts.join(" ") : "—";
    }

    function renderCcyBadges(totalsByCcy) {
      const parts = Object.keys(totalsByCcy || {}).sort().map((k) => "<span class=\\"badge\\">" + escapeHtml(k) + " " + money(totalsByCcy[k]) + "</span>");
      return parts.length ? parts.join("") : "";
    }

    function vatByRateHtml(m) {
      const keys = Object.keys(m || {});
      if (!keys.length) return "";
      keys.sort((a, b) => {
        if (a === "unknown") return 1;
        if (b === "unknown") return -1;
        const na = Number(a), nb = Number(b);
        if (!isNaN(na) && !isNaN(nb)) return nb - na;
        return String(a).localeCompare(String(b));
      });
      const parts = [];
      for (const k of keys) {
        const x = m[k];
        if (!x) continue;
        const label = k === "unknown" ? OVI.vatRateUnknown : (k + " %");
        parts.push(escapeHtml(label) + ": " + String(x.receipt_count) + "× · CZK " + money(x.vat_czk));
      }
      return parts.length ? "<div class=\\"stat-note ov-period-stems\\">" + parts.join(" · ") + "</div>" : "";
    }

    function merchCell(row) {
      const mh = row.merchant_hint != null ? String(row.merchant_hint) : "";
      if (!row.id) return "<td>" + escapeHtml(mh) + "</td>";
      return "<td><input type=\\"text\\" class=\\"ov-merch-input\\" data-rid=\\"" + escapeHtml(row.id) + "\\" data-orig=\\"" + escapeHtml(mh) + "\\" value=\\"" + escapeHtml(mh) + "\\" /></td>";
    }

    function totalCell(row) {
      if (!row.id) {
        const a = (row.currency || "") + " " + (row.total != null ? money(row.total) : "—");
        return "<td class=\\"num\\">" + escapeHtml(a.trim() || "—") + "</td>";
      }
      const ccy = escapeHtml(row.currency || "");
      const tv = row.total != null ? String(row.total) : "";
      return "<td class=\\"num\\">" + ccy + " <input type=\\"number\\" step=\\"any\\" class=\\"ov-rcpt-patch\\" data-field=\\"total\\" data-rid=\\"" + escapeHtml(row.id) + "\\" data-orig=\\"" + escapeHtml(tv) + "\\" value=\\"" + escapeHtml(tv) + "\\"/></td>";
    }
    function vatRateCell(row) {
      if (!row.id) {
        const rateDisp = row.vat_rate != null && row.vat_rate !== "" ? escapeHtml(String(row.vat_rate)) + "%" : "—";
        return "<td class=\\"num\\">" + rateDisp + "</td>";
      }
      const vr = row.vat_rate != null && row.vat_rate !== "" ? String(row.vat_rate) : "";
      return "<td class=\\"num\\"><input type=\\"number\\" step=\\"any\\" min=\\"0\\" max=\\"100\\" class=\\"ov-rcpt-patch\\" data-field=\\"vat_rate\\" data-rid=\\"" + escapeHtml(row.id) + "\\" data-orig=\\"" + escapeHtml(vr) + "\\" value=\\"" + escapeHtml(vr) + "\\"/></td>";
    }
    function vatAmountCell(row) {
      if (!row.id) {
        const vatDisp = row.vat_amount != null ? escapeHtml(String(row.currency || "")) + " " + money(row.vat_amount) : "—";
        return "<td class=\\"num\\">" + vatDisp + "</td>";
      }
      const va = row.vat_amount != null ? String(row.vat_amount) : "";
      return "<td class=\\"num\\"><input type=\\"number\\" step=\\"any\\" min=\\"0\\" class=\\"ov-rcpt-patch\\" data-field=\\"vat_amount\\" data-rid=\\"" + escapeHtml(row.id) + "\\" data-orig=\\"" + escapeHtml(va) + "\\" value=\\"" + escapeHtml(va) + "\\"/></td>";
    }

    function wireMerchInputs(root) {
      root.querySelectorAll("input.ov-merch-input").forEach((inp) => {
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); });
        inp.addEventListener("blur", async () => {
          const rid = inp.getAttribute("data-rid");
          const orig = inp.getAttribute("data-orig") || "";
          const trimmed = inp.value.trim();
          if (trimmed === orig) return;
          const res = await fetch("/api/receipts/" + encodeURIComponent(rid), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ merchant_hint: trimmed || null })
          });
          if (!res.ok) {
            const t = await res.text();
            alert(OVI.merchSaveFail + " " + res.status + " " + t);
            inp.value = orig;
            return;
          }
          inp.setAttribute("data-orig", trimmed);
        });
      });
    }

    function wireRcptPatchInputs(root) {
      root.querySelectorAll("input.ov-rcpt-patch").forEach((inp) => {
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); });
        inp.addEventListener("blur", async () => {
          const rid = inp.getAttribute("data-rid");
          const field = inp.getAttribute("data-field");
          const orig = inp.getAttribute("data-orig") || "";
          const trimmed = inp.value.trim().replace(",", ".");
          if (trimmed === (orig || "").trim().replace(",", ".")) return;
          let payload = null;
          if (field === "total") {
            if (trimmed === "") {
              inp.value = orig;
              return;
            }
            const n = Number(trimmed);
            if (Number.isNaN(n)) { inp.value = orig; return; }
            payload = { total: n };
          } else if (field === "vat_rate") {
            if (trimmed === "") payload = { vat_rate: null };
            else {
              const n = Number(trimmed);
              if (Number.isNaN(n)) { inp.value = orig; return; }
              payload = { vat_rate: n };
            }
          } else if (field === "vat_amount") {
            if (trimmed === "") payload = { vat_amount: null };
            else {
              const n = Number(trimmed);
              if (Number.isNaN(n)) { inp.value = orig; return; }
              payload = { vat_amount: n };
            }
          }
          if (!payload) return;
          const res = await fetch("/api/receipts/" + encodeURIComponent(rid), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          if (!res.ok) {
            const t = await res.text();
            alert(OVI.rcptSaveFail + " " + res.status + " " + t);
            inp.value = orig;
            return;
          }
          if (field === "total") inp.setAttribute("data-orig", trimmed);
          else if (field === "vat_rate") inp.setAttribute("data-orig", trimmed === "" ? "" : String(payload.vat_rate != null ? payload.vat_rate : ""));
          else if (field === "vat_amount") inp.setAttribute("data-orig", trimmed === "" ? "" : String(payload.vat_amount != null ? payload.vat_amount : ""));
          load();
        });
      });
    }

    let ovPreviewUrl = null;
    function revokeOvPreview() {
      if (ovPreviewUrl) {
        URL.revokeObjectURL(ovPreviewUrl);
        ovPreviewUrl = null;
      }
      const img = document.getElementById("ov-rcpt-dlg-img");
      const fr = document.getElementById("ov-rcpt-dlg-frame");
      img.removeAttribute("src");
      img.hidden = true;
      fr.removeAttribute("src");
      fr.hidden = true;
    }
    async function openReceiptPreview(rid) {
      revokeOvPreview();
      const url = "/api/receipts/" + encodeURIComponent(rid) + "/file";
      const res = await fetch(url);
      if (!res.ok) {
        alert(OVI.previewErr + " " + res.status);
        return;
      }
      const ct = (res.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
      const blob = await res.blob();
      ovPreviewUrl = URL.createObjectURL(blob);
      const img = document.getElementById("ov-rcpt-dlg-img");
      const fr = document.getElementById("ov-rcpt-dlg-frame");
      const dlg = document.getElementById("ov-rcpt-dlg");
      if (ct.startsWith("image/")) {
        img.src = ovPreviewUrl;
        img.hidden = false;
        fr.hidden = true;
      } else {
        fr.src = ovPreviewUrl;
        fr.hidden = false;
        img.hidden = true;
      }
      if (typeof dlg.showModal === "function") dlg.showModal();
    }
    function wirePreviewOpen(root) {
      root.querySelectorAll("button.ov-rcpt-open").forEach((btn) => {
        btn.onclick = () => openReceiptPreview(btn.getAttribute("data-rid"));
      });
    }

    async function load() {
      const el = document.getElementById("overview-app");
      el.innerHTML = "<p class=\\"empty\\">" + escapeHtml(OVI.loading) + "</p>";
      const r = await fetch("/api/overview");
      if (!r.ok) { el.innerHTML = "<p class=\\"empty\\">" + escapeHtml(OVI.loadErr) + "</p>"; return; }
      const d = await r.json();
      const lbl = d.category_labels_cs || {};

      let html = "";
      html += "<div class=\\"income-flow-block\\"><div class=\\"income-flow-label\\">" + escapeHtml(OVI.secExpenses) + "</div><div class=\\"summary-grid\\">";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statPeriods) + "</div><div class=\\"value\\">" + d.month_count + "</div>";
      if (d.period_bucket_stems && d.period_bucket_stems.length) {
        html += "<div class=\\"stat-note ov-period-stems\\">" + d.period_bucket_stems.map((s) => escapeHtml(String(s))).join(" · ") + "</div>";
      }
      html += "</div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statReceipts) + "</div><div class=\\"value\\">" + d.total_receipts + "</div></div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statCcy) + "</div><div class=\\"stat-badges\\">" + renderGrandCcy(d.grand_totals_by_currency) + "</div></div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statCzk) + "</div><div class=\\"value\\">" + (d.grand_total_czk != null ? money(d.grand_total_czk) : "—") + "</div><div class=\\"stat-note\\">" + escapeHtml(OVI.fxHint) + "</div></div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statVatCzk) + "</div><div class=\\"value\\">" + money(d.grand_vat_czk != null ? d.grand_vat_czk : 0) + "</div>";
      const gvCcy = d.grand_vat_totals_by_currency || {};
      if (Object.keys(gvCcy).length) html += "<div class=\\"stat-badges\\">" + renderGrandCcy(gvCcy) + "</div>";
      html += vatByRateHtml(d.receipt_vat_by_rate);
      if ((d.fx_skipped_vat_receipts || 0) > 0) html += "<div class=\\"stat-note\\">" + escapeHtml(OVI.fxSkippedVat.replace("{n}", String(d.fx_skipped_vat_receipts))) + "</div>";
      html += "</div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statTrips) + "</div><div class=\\"value\\">" + (d.travel_count || 0) + "</div></div>";
      html += "</div></div>";
      const sk = (d.fx_skipped_receipts || 0) + (d.fx_skipped_trips || 0);
      if (sk > 0) {
        html += "<p class=\\"month-meta\\" style=\\"margin:-0.5rem 0 var(--gap)\\">" + escapeHtml(OVI.fxSkipped.replace("{r}", String(d.fx_skipped_receipts || 0)).replace("{t}", String(d.fx_skipped_trips || 0))) + "</p>";
      }

      const inc = d.income;
      if (inc && inc.file_count > 0) {
        html += "<div class=\\"income-flow-block\\"><div class=\\"income-flow-label\\">" + escapeHtml(OVI.secIncome) + "</div><div class=\\"summary-grid\\">";
        html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.incomePaidCzk) + "</div><div class=\\"value\\">" + money(inc.income_grand_total_czk_paid) + "</div></div>";
        html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.netCzk) + "</div><div class=\\"value\\">" + money(inc.net_czk) + "</div></div>";
        html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.incomePending) + "</div><div class=\\"value\\">" + String(inc.pending_count || 0) + "</div>";
        const pccy = inc.pending_totals_by_currency || {};
        if (Object.keys(pccy).length) html += "<div class=\\"stat-badges\\">" + renderGrandCcy(pccy) + "</div>";
        html += "</div>";
        html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.statInvoiceMonths) + "</div><div class=\\"value\\">" + String(inc.invoice_month_count || 0) + "</div>";
        if (inc.invoice_month_stems && inc.invoice_month_stems.length) {
          html += "<div class=\\"stat-note ov-period-stems\\">" + inc.invoice_month_stems.map((s) => escapeHtml(String(s))).join(" · ") + "</div>";
        }
        html += "</div></div>";
        const share = inc.month_share_paid_czk || [];
        if (share.length) {
          html += "<div class=\\"income-flow-block\\"><div class=\\"income-flow-label\\">" + escapeHtml(OVI.incomeMonthShare) + "</div><div class=\\"income-flow-badges\\">";
          for (const s of share) {
            html += "<span class=\\"badge\\">" + escapeHtml(String(s.year_month)) + " · " + escapeHtml(String(s.pct_of_paid_total)) + "% · CZK " + money(s.total_czk) + "</span>";
          }
          html += "</div></div>";
        }
        if ((inc.income_fx_skipped || 0) > 0) {
          html += "<p class=\\"month-meta\\" style=\\"margin:-0.5rem 0 var(--gap)\\">" + escapeHtml(OVI.incomeFxSkipped.replace("{n}", String(inc.income_fx_skipped))) + "</p>";
        }
        html += "<div class=\\"panel\\"><details class=\\"panel-details ov-income-details\\"><summary><span class=\\"ov-income-sum-title\\">" + escapeHtml(OVI.incomePanel) + "</span><a class=\\"file-link\\" href=\\"/income\\" onclick=\\"event.stopPropagation(); return true;\\">" + escapeHtml(OVI.incomeEditLink) + "</a></summary><div class=\\"body\\">";
        if (!(inc.months || []).length) {
          html += "<p class=\\"empty\\" style=\\"margin:0\\">—</p>";
        } else {
          for (const im of inc.months) {
            const ccyB = renderCcyBadges(im.totals_by_currency);
            html += "<details class=\\"month\\"><summary>" + escapeHtml(String(im.year_month)) + " <span class=\\"month-meta month-meta-badges\\">" + (ccyB || "—") + "<span class=\\"badge\\">CZK " + money(im.total_czk) + "</span></span></summary>";
            html += "<div class=\\"month-inner\\"><div class=\\"ov-rcpt-scroll\\"><table><thead><tr>";
            html += "<th>" + escapeHtml(OVI.incomeThNo) + "</th><th>" + escapeHtml(OVI.incomeThClient) + "</th><th class=\\"num\\">" + escapeHtml(OVI.incomeThAmt) + "</th><th class=\\"num\\">" + escapeHtml(OVI.incomeThCzk) + "</th>";
            html += "<th>" + escapeHtml(OVI.incomeThInvDt) + "</th><th>" + escapeHtml(OVI.incomeThPayDt) + "</th><th>" + escapeHtml(OVI.incomeThCnb) + "</th></tr></thead><tbody>";
            for (const it of (im.items || [])) {
              const iam = (it.amount != null && it.currency) ? (escapeHtml(it.currency) + " " + money(it.amount)) : "—";
              const icz = it.amount_czk != null ? money(it.amount_czk) : "—";
              const ipd = it.payment_date ? escapeHtml(it.payment_date) : "—";
              const ira = it.cnb_valuation_date ? escapeHtml(it.cnb_valuation_date) : "—";
              html += "<tr><td>" + escapeHtml(it.invoice_number || "—") + "</td><td>" + escapeHtml(it.client_name || "—") + "</td><td class=\\"num\\">" + iam + "</td><td class=\\"num\\">" + icz + "</td>";
              html += "<td>" + escapeHtml(it.invoice_date || "—") + "</td><td>" + ipd + "</td><td>" + ira + "</td></tr>";
            }
            html += "</tbody></table></div></div></details>";
          }
        }
        html += "</div></details></div>";
        html += "</div>";
      }

      html += "<div class=\\"income-flow-block\\"><div class=\\"income-flow-label\\">" + escapeHtml(OVI.secObligations) + "</div><div class=\\"summary-grid\\">";
      const obl = d.obligations || {};
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.oblPaid) + "</div><div class=\\"value\\">" + money(obl.paid_czk != null ? obl.paid_czk : 0) + "</div></div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.oblUnpaid) + "</div><div class=\\"value\\">" + money(obl.unpaid_czk != null ? obl.unpaid_czk : 0) + "</div><div class=\\"stat-note\\"><a class=\\"file-link\\" href=\\"/obligations\\">" + escapeHtml(OVI.oblLink) + "</a></div></div>";
      html += "<div class=\\"stat\\"><div class=\\"label\\">" + escapeHtml(OVI.netAfterObl) + "</div><div class=\\"value\\">" + money(d.net_czk_after_obligations != null ? d.net_czk_after_obligations : d.income && d.income.net_czk != null ? d.income.net_czk : null) + "</div><div class=\\"stat-note\\">" + escapeHtml(OVI.netAfterOblHint) + "</div></div>";
      html += "</div></div>";

      html += "<div class=\\"income-flow-block\\"><div class=\\"income-flow-label\\">" + escapeHtml(OVI.secDetail) + "</div>";
      html += "<div class=\\"panel\\"><h2>" + escapeHtml(OVI.grandCat) + "</h2><div class=\\"body\\">" + renderCatTable(d.grand_totals_by_category, lbl) + "</div></div>";

      if (!(d.months || []).length) {
        html += "<p class=\\"empty\\">" + escapeHtml(OVI.noMonths) + "</p>";
      } else {
      for (const m of d.months) {
        const ccyB = renderCcyBadges(m.totals_by_currency);
        html += "<details class=\\"month\\" open>";
        html += "<summary>" + escapeHtml(String(m.year_month)) + " <span class=\\"month-meta month-meta-badges\\">" + (ccyB || "—") + "<span class=\\"badge\\">" + String(m.receipt_count) + " " + escapeHtml(OVI.rcptSuffix) + "</span></span></summary>";
        html += "<div class=\\"month-inner\\">";
        html += "<h3 class=\\"month-h3\\">" + escapeHtml(OVI.byCat) + "</h3>";
        html += renderCatTable(m.totals_by_category, lbl);
        html += "<h3 class=\\"month-h3\\">" + escapeHtml(OVI.receipts) + "</h3>";
        html += "<div class=\\"ov-rcpt-scroll\\"><table class=\\"ov-rcpt-table\\"><thead><tr><th>" + escapeHtml(OVI.thDate) + "</th><th>" + escapeHtml(OVI.thFile) + "</th><th>" + escapeHtml(OVI.thMerch) + "</th><th>" + escapeHtml(OVI.thCat2) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thAmt) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thVatRate) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thVat) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thVatCzk) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thCzk) + "</th><th>" + escapeHtml(OVI.thPrev) + "</th></tr></thead><tbody>";
        for (const row of (m.receipts || [])) {
          const prev = row.id ? "<button type=\\"button\\" class=\\"file-link ov-rcpt-open\\" data-rid=\\"" + escapeHtml(row.id) + "\\">" + escapeHtml(OVI.open) + "</button>" : "—";
          const czkCell = row.amount_czk != null ? money(row.amount_czk) : "—";
          const vatCzkDisp = row.vat_amount_czk != null ? money(row.vat_amount_czk) : "—";
          html += "<tr><td>" + escapeHtml(row.date) + "</td><td>" + escapeHtml(row.source_file) + "</td>" + merchCell(row) + "<td>" + escapeHtml(row.category_label_cs) + "</td>" + totalCell(row) + vatRateCell(row) + vatAmountCell(row) + "<td class=\\"num\\">" + escapeHtml(vatCzkDisp) + "</td><td class=\\"num\\">" + escapeHtml(czkCell) + "</td><td>" + prev + "</td></tr>";
        }
        html += "</tbody></table></div></div></details>";
      }
      }
      const typeLabTravel = { meal_allowance_cz: OVI.typeMeal, actual_meals: OVI.typeActual, mixed: OVI.typeMix };
      const tt = d.travel_trips || [];
      const ttot = d.travel_totals_by_currency || {};
      html += "<div class=\\"panel\\"><h2>" + escapeHtml(OVI.travelPanel) + " <a class=\\"file-link\\" href=\\"/travel\\">" + escapeHtml(OVI.edit) + "</a></h2><div class=\\"body\\">";
      if (!tt.length) {
        html += "<p class=\\"empty\\" style=\\"margin:0\\">" + escapeHtml(OVI.travelEmpty) + " <a href=\\"/travel\\">" + escapeHtml(OVI.addTrip) + "</a></p>";
      } else {
        const ttotStr = Object.keys(ttot).sort().map((k) => k + " " + money(ttot[k])).join(" · ");
        const refY = d.travel_stravne_reference_year ? (OVI.tsumRefMid + d.travel_stravne_reference_year) : "";
        html += "<p class=\\"month-meta\\" style=\\"margin:0 0 0.75rem\\">" + escapeHtml(OVI.tsumPrefix) + escapeHtml(refY) + ": " + escapeHtml(ttotStr || "—") + " " + escapeHtml(OVI.tsumSuffix) + " <strong>" + escapeHtml(OVI.suggestEm) + "</strong> " + escapeHtml(OVI.suggestTail) + "</p>";
        if (d.travel_stravne_legal_note_cs) html += "<p class=\\"month-meta\\" style=\\"margin:0 0 0.75rem\\">" + escapeHtml(d.travel_stravne_legal_note_cs) + "</p>";
        html += "<table><thead><tr><th>" + escapeHtml(OVI.thTrPer) + "</th><th>" + escapeHtml(OVI.thTrTo) + "</th><th>" + escapeHtml(OVI.thTrType) + "</th><th class=\\"num\\">" + escapeHtml(OVI.thTrAmt) + "</th><th>" + escapeHtml(OVI.thTrNotes) + "</th></tr></thead><tbody>";
        for (const t of tt) {
          let amt = t.amount_effective != null ? money(t.amount_effective) + " " + (t.currency_effective || "") : "—";
          if (t.is_stravne_estimate && t.amount_effective != null) amt += " " + OVI.estimate;
          const where = [t.country_code, t.destination].filter(Boolean).join(" ") || "—";
          const tip = escapeHtml(t.stravne_detail_cs || "");
          html += "<tr><td>" + escapeHtml(t.date_from) + " – " + escapeHtml(t.date_to) + "</td><td>" + escapeHtml(where) + "<br/><small>" + escapeHtml(t.purpose || "") + "</small></td><td>" + escapeHtml(typeLabTravel[t.claim_type] || t.claim_type) + "</td><td class=\\"num\\" title=\\"" + tip + "\\">" + escapeHtml(amt) + "</td><td>" + escapeHtml(t.notes || "—") + "</td></tr>";
        }
        html += "</tbody></table>";
      }
      html += "</div></div>";
      html += "</div>";

      el.innerHTML = html;
      wireMerchInputs(el);
      wireRcptPatchInputs(el);
      wirePreviewOpen(el);
    }

    (function () {
      const dlg = document.getElementById("ov-rcpt-dlg");
      document.getElementById("ov-rcpt-dlg-close").onclick = () => dlg.close();
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
      dlg.addEventListener("close", revokeOvPreview);
    })();

    document.getElementById("reload").onclick = load;
    document.getElementById("ov-refresh-vat").onclick = async () => {
      if (!confirm(OVI.reloadVatConfirm)) return;
      const res = await fetch("/api/receipts/refresh-vat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: false })
      });
      let j = {};
      try { j = await res.json(); } catch (e) {}
      if (!res.ok) {
        alert(OVI.reloadVatErr + " " + res.status + " " + (j.detail || ""));
        return;
      }
      alert(OVI.reloadVatDone.replace("{receipts}", String(j.updated_receipts != null ? j.updated_receipts : 0)).replace("{files}", String(j.updated_files != null ? j.updated_files : 0)));
      load();
    };
    (function () {
      const KEY = "ov_privacy_blur";
      const btn = document.getElementById("ov-privacy-toggle");
      function sync() {
        const on = document.body.classList.contains("ov-privacy-blur");
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.textContent = on ? OVI.privacyShow : OVI.privacyHide;
      }
      if (sessionStorage.getItem(KEY) === "1") document.body.classList.add("ov-privacy-blur");
      sync();
      btn.onclick = () => {
        document.body.classList.toggle("ov-privacy-blur");
        sessionStorage.setItem(KEY, document.body.classList.contains("ov-privacy-blur") ? "1" : "0");
        sync();
      };
    })();
    load();
  </script>
</body>
</html>
"""
)


def search_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    si = search_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("search.title"))
        + """</title>
    """
        + APP_SHELL_CSS
        + """
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main">
      <h1>"""
        + html.escape(L("search.h1"))
        + """</h1>
      <p class="lead">"""
        + L("search.lead")
        + """</p>
      <p class="lead">"""
        + L("search.rules_path")
        + """</p>
      <div class="btn-row">
        <input type="search" id="q" placeholder=\""""
        + html.escape(L("search.placeholder"))
        + """\"/>
        <button type="button" class="btn-primary" id="go">"""
        + html.escape(L("search.btn"))
        + """</button>
      </div>
      <p id="status" class="lead"></p>
      <div id="out"></div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const SI = """
        + si
        + """;
      function esc(s) {
        if (s == null) return "";
        return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
      }
      async function run() {
        const q = document.getElementById("q").value.trim();
        const st = document.getElementById("status");
        const out = document.getElementById("out");
        out.innerHTML = "";
        if (!q) { st.textContent = ""; return; }
        st.textContent = SI.loading;
        const r = await fetch("/api/search?q=" + encodeURIComponent(q));
        const rows = await r.json();
        if (!r.ok) { st.textContent = SI.err; return; }
        st.textContent = rows.length ? (rows.length + " " + SI.found) : SI.none;
        if (!rows.length) return;
        let h = "<table><thead><tr>";
        h += "<th>" + esc(SI.thDate) + "</th><th>" + esc(SI.thFile) + "</th><th>" + esc(SI.thMerch) + "</th>";
        h += "<th>" + esc(SI.thCat) + "</th><th>" + esc(SI.thAmt) + "</th><th>" + esc(SI.thVat) + "</th><th>" + esc(SI.thBucket) + "</th><th>" + esc(SI.thLink) + "</th>";
        h += "</tr></thead><tbody>";
        for (const row of rows) {
          const amt = row.total != null ? esc(row.currency) + " " + esc(String(row.total)) : "—";
          const vat = row.vat_amount != null ? esc(row.currency) + " " + esc(String(row.vat_amount)) : "—";
          const lk = row.id ? "<a href=\\"/api/receipts/" + encodeURIComponent(row.id) + "/file\\">" + esc(SI.open) + "</a>" : "—";
          h += "<tr><td>" + esc(row.date) + "</td><td>" + esc(row.source_file) + "</td><td>" + esc(row.merchant_hint) + "</td>";
          h += "<td>" + esc(row.category) + "</td><td>" + amt + "</td><td>" + vat + "</td><td>" + esc(row.bucket_file) + "</td><td>" + lk + "</td></tr>";
        }
        h += "</tbody></table>";
        out.innerHTML = h;
      }
      document.getElementById("go").onclick = run;
      document.getElementById("q").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    </script>
  </body>
</html>
"""
    )


def income_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ii = income_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>"""
        + html.escape(L("income.title"))
        + """</title>
  """
        + APP_SHELL_CSS
        + """
  <style>
    .travel-main .hint { margin: 0 0 var(--gap-sm); max-width: 52rem; }
    .travel-main label { display: block; font-weight: 600; font-size: 0.88rem; }
    .travel-main .dir-row { display: flex; flex-wrap: wrap; gap: var(--gap-sm); align-items: flex-end; margin-top: var(--gap-sm); }
    .travel-main .dir-row input[type=text] { flex: 1; min-width: 12rem; }
    .travel-main table { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: var(--gap); background: var(--card); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
    .travel-main th, .travel-main td { text-align: left; padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    .travel-main th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; background: var(--surface); }
    #income_status { margin: var(--gap-sm) 0 0; font-size: 0.88rem; color: var(--muted); }
  </style>
</head>
<body>
  """
        + nav_html(lang, np)
        + """
  <main class="app-main travel-main">
  <h1>"""
        + html.escape(L("income.h1"))
        + """</h1>
  <p class="lead">"""
        + L("income.lead")
        + """</p>
  <label>"""
        + html.escape(L("income.dir_lbl"))
        + """</label>
  <div class="dir-row">
    <input type="text" id="inv_dir" autocomplete="off"/>
    <button type="button" class="btn-primary" id="btn_dir">"""
        + html.escape(L("income.dir_btn"))
        + """</button>
    <button type="button" class="btn-secondary" id="btn_reload">"""
        + html.escape(L("income.reload"))
        + """</button>
  </div>
  <p id="income_status"></p>
  <div id="inv_table"><p class="hint">…</p></div>
  <h2>"""
        + html.escape(L("income.m.h2"))
        + """</h2>
  <p class="lead">"""
        + L("income.m.lead")
        + """</p>
  <div id="inv_m"></div>
  <h2>"""
        + html.escape(L("income.m2.h2"))
        + """</h2>
  <p class="lead">"""
        + L("income.m2.lead")
        + """</p>
  <div id="inv_m2"></div>
  <h2>"""
        + html.escape(L("income.q.h2"))
        + """</h2>
  <p class="lead">"""
        + L("income.q.lead")
        + """</p>
  <p class="hint">"""
        + L("income.q.json")
        + """</p>
  <div id="inv_q"></div>
  </main>
  """
        + NAV_HIGHLIGHT_SCRIPT
        + """
  <script>
    const II = """
        + ii
        + """;
    const inf = new Intl.NumberFormat(II.locale || "cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    function esc(s) {
      if (s == null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }
    function dicInputValue(row) {
      if (row.client_dic) return row.client_dic;
      if ((row.country_hint || "").toUpperCase() === "CZ" && row.client_ico) return II.icoPrefix + " " + row.client_ico;
      return "";
    }
    function vatInputValue(row) {
      return row.client_vat || "";
    }
    function renderMonthly(ma, boxId, emptyMsg) {
      const box = document.getElementById(boxId || "inv_m");
      if (!box) return;
      const months = (ma && ma.months) ? ma.months : [];
      if (!months.length) {
        box.innerHTML = "<p class=\\"hint\\">" + esc(emptyMsg || II.mEmpty) + "</p>";
        return;
      }
      let h = "<p class=\\"hint\\">" + esc(II.mSummary
        .replace("{inc}", inf.format(ma.income_total_czk_paid || 0))
        .replace("{n}", String(ma.month_count || 0))
        .replace("{avg}", inf.format(ma.avg_income_czk || 0))
        .replace("{exp}", inf.format(ma.expenses_total_czk || 0))
        .replace("{net}", inf.format(ma.approx_net_total_czk != null ? ma.approx_net_total_czk : 0))) + "</p>";
      h += "<table><thead><tr>";
      h += "<th>" + esc(II.mThMonth) + "</th><th class=\\"num\\">" + esc(II.mThInc) + "</th>";
      h += "<th class=\\"num\\">" + esc(II.mThExp) + "</th><th class=\\"num\\">" + esc(II.mThNet) + "</th>";
      h += "</tr></thead><tbody>";
      for (const m of months) {
        h += "<tr><td>" + esc(m.year_month || "") + "</td>";
        h += "<td class=\\"num\\">" + esc(inf.format(m.avg_income_czk || 0)) + "</td>";
        h += "<td class=\\"num\\">" + esc(inf.format(m.expenses_czk || 0)) + "</td>";
        h += "<td class=\\"num\\">" + esc(inf.format(m.approx_net_czk || 0)) + "</td></tr>";
      }
      h += "</tbody></table>";
      box.innerHTML = h;
    }
    function fmtOrigTotals(totals) {
      const keys = Object.keys(totals || {}).sort();
      if (!keys.length) return "—";
      return keys.map((k) => k + " " + inf.format(totals[k])).join(" + ");
    }
    function quarterTitle(y, q, un) {
      if (un) return II.qUnassigned;
      const pat = [II.qQ1, II.qQ2, II.qQ3, II.qQ4][q - 1];
      return pat.replace("{year}", String(y));
    }
    function formatQLine(x) {
      const no = x.invoice_number != null ? String(x.invoice_number) : "—";
      const amt = x.amount != null ? String(x.amount) : "—";
      const ccy = x.currency || "";
      const czk = x.amount_czk != null ? inf.format(x.amount_czk) : "—";
      const cnb = x.cnb_valuation_date || "—";
      const pay = x.payment_date || x.paid_month || "—";
      let s = II.qLine
        .replace(/\{no\}/g, esc(no))
        .replace(/\{amt\}/g, esc(amt))
        .replace(/\{ccy\}/g, esc(ccy))
        .replace(/\{czk\}/g, esc(czk))
        .replace(/\{cnb\}/g, esc(cnb))
        .replace(/\{pay\}/g, esc(pay));
      if (x.id) {
        s += " <a href=\\"/api/income-invoices/" + encodeURIComponent(x.id) + "/file\\" target=\\"_blank\\">" + esc(II.qPdf) + "</a>";
      }
      return s;
    }
    function renderQuarterly(qf) {
      const box = document.getElementById("inv_q");
      if (!box) return;
      const sections = (qf && qf.sections) ? qf.sections : [];
      if (!sections.length) {
        box.innerHTML = "<p class=\\"hint\\">" + esc(II.qEmpty) + "</p>";
        return;
      }
      let h = "";
      for (const sec of sections) {
        h += "<h3>" + esc(quarterTitle(sec.year, sec.quarter, sec.unassigned)) + "</h3>";
        if (sec.groups.some((g) => g.has_czk_gap)) {
          h += "<p class=\\"hint\\">" + esc(II.qGap) + "</p>";
        }
        h += "<table><thead><tr>";
        h += "<th>" + esc(II.qThVat) + "</th><th>" + esc(II.qThName) + "</th><th>" + esc(II.qThN) + "</th>";
        h += "<th>" + esc(II.qThOrig) + "</th><th class=\\"num\\">" + esc(II.qThCzk) + "</th><th>" + esc(II.qThNote) + "</th></tr></thead><tbody>";
        for (const g of sec.groups) {
          const vatCell = g.client_vat ? esc(g.client_vat) : esc(II.qNoVat);
          h += "<tr><td>" + vatCell + "</td><td>" + esc(g.client_name) + "</td><td class=\\"num\\">" + esc(String(g.invoice_count)) + "</td>";
          h += "<td>" + esc(fmtOrigTotals(g.totals_by_currency)) + "</td><td class=\\"num\\">" + esc(inf.format(g.total_czk)) + "</td><td><small>";
          const lines = g.lines || [];
          if (lines.length === 0) h += "—";
          else if (lines.length === 1) h += formatQLine(lines[0]);
          else {
            h += "<ul>";
            for (const x of lines) h += "<li>" + formatQLine(x) + "</li>";
            h += "</ul>";
          }
          h += "</small></td></tr>";
        }
        h += "</tbody><tfoot><tr><td colspan=\\"4\\">" + esc(II.qTfoot) + "</td><td class=\\"num\\">" + esc(inf.format(sec.quarter_total_czk)) + "</td><td></td></tr></tfoot></table>";
      }
      box.innerHTML = h;
    }
    async function saveRow(id, paid, paidMonth, payDate, dic, vat, inApprox) {
      const st = document.getElementById("income_status");
      const r = await fetch("/api/income-invoices/" + encodeURIComponent(id), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          paid: paid,
          paid_month: paidMonth || null,
          payment_date: payDate || null,
          client_dic: dic,
          client_vat: vat,
          in_approx_selected: !!inApprox,
        }),
      });
      if (!r.ok) { st.textContent = II.rowErr; return; }
      st.textContent = II.rowSaved;
      loadTable();
    }
    function wireRow(tr, row) {
      const paidSel = tr.querySelector(".inv-paid");
      const pm = tr.querySelector(".inv-paid-m");
      const pdt = tr.querySelector(".inv-pay-d");
      const dic = tr.querySelector(".inv-dic");
      const vat = tr.querySelector(".inv-vat");
      const approx = tr.querySelector(".inv-approx2");
      const sync = () => saveRow(
        row.id,
        paidSel.value === "1",
        pm.value || null,
        pdt.value || null,
        (dic && dic.value != null) ? String(dic.value).trim() : "",
        (vat && vat.value != null) ? String(vat.value).trim() : "",
        approx ? approx.checked : false
      );
      paidSel.addEventListener("change", sync);
      pm.addEventListener("change", sync);
      pdt.addEventListener("change", sync);
      if (dic) { dic.addEventListener("change", sync); }
      if (vat) { vat.addEventListener("change", sync); }
      if (approx) { approx.addEventListener("change", sync); }
    }
    async function loadTable() {
      const box = document.getElementById("inv_table");
      const st = document.getElementById("income_status");
      const mbox = document.getElementById("inv_m");
      const m2box = document.getElementById("inv_m2");
      const qbox = document.getElementById("inv_q");
      box.innerHTML = "<p class=\\"hint\\">" + esc(II.loadRows) + "</p>";
      if (mbox) mbox.innerHTML = "<p class=\\"hint\\">" + esc(II.loadRows) + "</p>";
      if (m2box) m2box.innerHTML = "<p class=\\"hint\\">" + esc(II.loadRows) + "</p>";
      if (qbox) qbox.innerHTML = "<p class=\\"hint\\">" + esc(II.loadRows) + "</p>";
      const r = await fetch("/api/income-invoices");
      if (!r.ok) {
        box.innerHTML = "<p class=\\"hint\\">" + esc(II.loadErr) + "</p>";
        if (mbox) mbox.innerHTML = "<p class=\\"hint\\">" + esc(II.loadErr) + "</p>";
        if (m2box) m2box.innerHTML = "<p class=\\"hint\\">" + esc(II.loadErr) + "</p>";
        if (qbox) qbox.innerHTML = "<p class=\\"hint\\">" + esc(II.loadErr) + "</p>";
        return;
      }
      const j = await r.json();
      document.getElementById("inv_dir").value = j.invoices_dir || "";
      if (mbox) renderMonthly(j.monthly_approx, "inv_m", II.mEmpty);
      if (m2box) renderMonthly(j.monthly_approx_selected, "inv_m2", II.m2Empty);
      if (qbox) renderQuarterly(j.quarterly_foreign);
      const rows = j.rows || [];
      if (!rows.length) { box.innerHTML = "<p class=\\"hint\\">" + esc(II.empty) + "</p>"; return; }
      let h = "<table><thead><tr>";
      h += "<th>" + esc(II.thNo) + "</th><th>" + esc(II.thClient) + "</th><th>" + esc(II.thFor) + "</th>";
      h += "<th>" + esc(II.thDate) + "</th><th>" + esc(II.thAmt) + "</th><th>" + esc(II.thCc) + "</th>";
      h += "<th>" + esc(II.thCz) + "</th><th>" + esc(II.thVat) + "</th>";
      h += "<th>" + esc(II.thPaid) + "</th><th>" + esc(II.thPaidM) + "</th><th>" + esc(II.thPayDate) + "</th><th class=\\"num\\">" + esc(II.thCzk) + "</th>";
      h += "<th>" + esc(II.thApprox2) + "</th><th>" + esc(II.thPdf) + "</th>";
      h += "</tr></thead><tbody>";
      for (const row of rows) {
        const amt = (row.amount != null && row.currency) ? (esc(row.currency) + " " + esc(String(row.amount))) : "—";
        const czkDisp = row.amount_czk != null ? inf.format(row.amount_czk) : "—";
        const err = row.scan_error ? ("<br><small>" + esc(II.scanErr) + ": " + esc(row.scan_error) + "</small>") : "";
        const pmVal = row.paid_month || "";
        const pdVal = row.payment_date || "";
        h += "<tr><td>" + esc(row.invoice_number || "—") + err + "</td>";
        h += "<td>" + esc(row.client_name || "—") + "</td><td>" + esc(row.for_who || "—") + "</td>";
        h += "<td>" + esc(row.invoice_date || "—") + "</td><td>" + amt + "</td><td>" + esc(row.country_hint || "") + "</td>";
        h += "<td><input type=\\"text\\" class=\\"inv-dic\\" value=\\"" + esc(dicInputValue(row)) + "\\" autocomplete=\\"off\\"/></td>";
        h += "<td><input type=\\"text\\" class=\\"inv-vat\\" value=\\"" + esc(vatInputValue(row)) + "\\" autocomplete=\\"off\\"/></td>";
        h += "<td><select class=\\"inv-paid\\" data-id=\\"" + esc(row.id) + "\\">";
        h += "<option value=\\"0\\"" + (row.paid ? "" : " selected") + ">" + esc(II.paidN) + "</option>";
        h += "<option value=\\"1\\"" + (row.paid ? " selected" : "") + ">" + esc(II.paidY) + "</option></select></td>";
        h += "<td><input type=\\"month\\" class=\\"inv-paid-m\\" value=\\"" + esc(pmVal) + "\\"/></td>";
        h += "<td><input type=\\"date\\" class=\\"inv-pay-d\\" value=\\"" + esc(pdVal) + "\\"/></td>";
        h += "<td class=\\"num\\">" + esc(czkDisp) + "</td>";
        h += "<td><input type=\\"checkbox\\" class=\\"inv-approx2\\"" + (row.in_approx_selected ? " checked" : "") + "/></td>";
        h += "<td><a href=\\"/api/income-invoices/" + encodeURIComponent(row.id) + "/file\\" target=\\"_blank\\">PDF</a></td></tr>";
      }
      h += "</tbody></table>";
      box.innerHTML = h;
      box.querySelectorAll("tbody tr").forEach((tr, i) => wireRow(tr, rows[i]));
    }
    document.getElementById("btn_reload").onclick = () => { loadTable(); document.getElementById("income_status").textContent = ""; };
    document.getElementById("btn_dir").onclick = async () => {
      const st = document.getElementById("income_status");
      const v = document.getElementById("inv_dir").value.trim();
      const r = await fetch("/api/income-invoices/dir", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invoices_dir: v }) });
      if (!r.ok) { st.textContent = II.dirErr; return; }
      st.textContent = II.savedDir;
      loadTable();
    };
    loadTable();
  </script>
</body>
</html>
"""
    )


def travel_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ti = travel_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>"""
        + html.escape(L("travel.title"))
        + """</title>
  """
        + APP_SHELL_CSS
    + """
  <style>
    .travel-main > .hint { margin: 0 0 var(--gap-sm); max-width: 42rem; }
    .travel-main > .hint:last-of-type { margin-bottom: var(--gap); }
    .travel-main label {
      display: block;
      font-weight: 600;
      font-size: 0.88rem;
      line-height: 1.3;
    }
    .travel-main label input, .travel-main label select, .travel-main label textarea {
      display: block;
      width: 100%;
      margin-top: var(--gap-xs);
      box-sizing: border-box;
    }
    .travel-main textarea { min-height: 5.5rem; }
    .travel-main #f > input[type=hidden] + label { margin-top: 0; }
    .travel-main #f > label { margin-top: var(--gap); }
    .travel-main #f > label:first-of-type { margin-top: 0; }
    .travel-main #f > .row2 { margin-top: var(--gap); }
    .travel-main #f > p { margin: var(--gap) 0 0; }
    .travel-main #f > p#stravne_preview { margin-top: var(--gap-sm); }
    .travel-main #f > .btn-primary, .travel-main #f > button:not(.btn-primary) { margin-top: var(--gap); }
    .travel-main .row2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--gap) var(--gap-sm);
      align-items: start;
    }
    @media (max-width: 520px) { .travel-main .row2 { grid-template-columns: 1fr; } }
    .travel-main .row2 label { margin-top: 0; }
    .travel-main button:not(.btn-primary) {
      margin-right: var(--gap-sm);
      padding: 0.5rem 0.85rem;
      cursor: pointer;
      border-radius: 3px;
      border: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--text);
      font-family: inherit;
      font-size: 0.88rem;
    }
    .travel-main #f .btn-row { margin-top: var(--gap-sm); }
    .travel-main table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
      margin-top: var(--gap);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 4px;
      overflow: hidden;
    }
    .travel-main th, .travel-main td { text-align: left; padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    .travel-main th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; background: var(--surface); }
    #status { margin: var(--gap-sm) 0 0; font-size: 0.88rem; color: var(--muted); }
    .travel-main > h2 { margin: var(--gap) 0 var(--gap-sm); font-size: 0.82rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
    .travel-card { padding: var(--pad-card-y) var(--pad-card-x); margin-bottom: var(--gap); }
  </style>
</head>
<body>
  """
        + nav_html(lang, np)
        + """
  <main class="app-main travel-main">
  <h1>"""
        + html.escape(L("travel.h1"))
        + """</h1>
  <p class="hint">"""
        + L("travel.hint1")
        + """</p>
  <p class="hint">"""
        + L("travel.json_path")
        + """ <code>/data/travel_allowances.json</code></p>

  <div class="travel-card">
  <form id="f">
    <input type="hidden" id="edit_id" value=""/>
    <label>"""
        + html.escape(L("travel.purpose"))
        + """<input type="text" id="purpose" placeholder=\""""
        + html.escape(L("travel.ph_purpose"))
        + """"/></label>
    <div class="row2">
      <label>"""
        + html.escape(L("travel.iso"))
        + """<input type="text" id="country_code" maxlength="2" placeholder="DE"/></label>
      <label>"""
        + html.escape(L("travel.city"))
        + """<input type="text" id="destination" placeholder=\""""
        + html.escape(L("travel.ph_city"))
        + """"/></label>
    </div>
    <div class="row2">
      <label>"""
        + html.escape(L("travel.from"))
        + """<input type="date" id="date_from" required/></label>
      <label>"""
        + html.escape(L("travel.to"))
        + """<input type="date" id="date_to" required/></label>
    </div>
    <label>"""
        + html.escape(L("travel.claim"))
        + """
      <select id="claim_type">
        <option value="meal_allowance_cz">"""
        + html.escape(L("travel.opt_meal"))
        + """</option>
        <option value="actual_meals">"""
        + html.escape(L("travel.opt_actual"))
        + """</option>
        <option value="mixed">"""
        + html.escape(L("travel.opt_mix"))
        + """</option>
      </select>
    </label>
    <div class="row2">
      <label>"""
        + html.escape(L("travel.amt"))
        + """<input type="number" step="any" id="amount_total" placeholder=\""""
        + html.escape(L("travel.ph_amt"))
        + """"/></label>
      <label>"""
        + html.escape(L("travel.ccy"))
        + """<input type="text" id="currency" value="CZK" maxlength="3"/></label>
    </div>
    <p id="stravne_preview" class="hint"></p>
    <div class="btn-row"><button type="button" class="btn-primary" id="stravne_fill">"""
        + html.escape(L("travel.fill_mf"))
        + """</button></div>
    <label>"""
        + html.escape(L("travel.note"))
        + """<textarea id="notes"></textarea></label>
    <button type="submit" class="btn-primary" id="btn_save">"""
        + html.escape(L("travel.save"))
        + """</button>
    <button type="button" id="btn_cancel" hidden>"""
        + html.escape(L("travel.cancel"))
        + """</button>
  </form>
  </div>
  <p id="status"></p>

  <h2>"""
        + html.escape(L("travel.list"))
        + """</h2>
  <div id="list"><p class="hint">"""
        + html.escape(L("travel.load_list"))
        + """</p></div>

  </main>
  """
        + NAV_HIGHLIGHT_SCRIPT
        + """
  <script>
    const TI = """
        + ti
        + """;
    const statusEl = document.getElementById("status");
    const nf = new Intl.NumberFormat(TI.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    async function refreshStravnePreview() {
      const el = document.getElementById("stravne_preview");
      const cc = document.getElementById("country_code").value.trim();
      const df = document.getElementById("date_from").value;
      const dt = document.getElementById("date_to").value;
      const ct = document.getElementById("claim_type").value;
      el.textContent = "";
      if (!df || !dt || !cc) return {};
      const u = "/api/stravne-suggest?country_code=" + encodeURIComponent(cc) + "&date_from=" + encodeURIComponent(df) + "&date_to=" + encodeURIComponent(dt) + "&claim_type=" + encodeURIComponent(ct);
      const r = await fetch(u);
      const j = await r.json();
      if (!j.ok) {
        if (j.reason === "no_rate") el.textContent = TI.noRate;
        return j;
      }
      el.textContent = TI.suggestLbl + " " + j.total + " " + j.currency + ". " + (j.detail_cs || "");
      return j;
    }

    function wireStravne() {
      ["country_code","date_from","date_to","claim_type"].forEach((id) => {
        document.getElementById(id).addEventListener("change", () => { refreshStravnePreview(); });
      });
      document.getElementById("country_code").addEventListener("input", () => { refreshStravnePreview(); });
      document.getElementById("stravne_fill").onclick = async () => {
        const j = await refreshStravnePreview();
        if (!j || !j.ok) return;
        document.getElementById("amount_total").value = j.total;
        document.getElementById("currency").value = j.currency;
      };
    }

    async function loadList() {
      const box = document.getElementById("list");
      const r = await fetch("/api/travel-trips");
      const trips = await r.json();
      if (!trips.length) {
        box.innerHTML = "<p class=\\"hint\\">" + esc(TI.emptyList) + "</p>";
        return;
      }
      let h = "<table><thead><tr><th>" + esc(TI.thPer) + "</th><th>" + esc(TI.thTo) + "</th><th>" + esc(TI.thType) + "</th><th>" + esc(TI.thAmt) + "</th><th></th></tr></thead><tbody>";
      const typeLab = { meal_allowance_cz: TI.typeMeal, actual_meals: TI.typeActual, mixed: TI.typeMix };
      for (const t of trips) {
        let amt = "—";
        const ad = t.amount_display;
        if (ad != null) {
          amt = nf.format(ad) + " " + (t.currency_display || "");
          if (t.is_stravne_estimate) amt += " " + TI.estimate;
        }
        const where = [t.country_code, t.destination].filter(Boolean).join(" ") || "—";
        h += "<tr><td>" + esc(t.date_from) + " – " + esc(t.date_to) + "</td><td>" + esc(where) + "<br/><small>" + esc(t.purpose||"") + "</small></td><td>" + esc(typeLab[t.claim_type]||t.claim_type) + "</td><td title=\\"" + esc(t.stravne_detail_cs||"") + "\\">" + esc(amt) + "</td><td><button type=\\"button\\" data-edit=\\"" + esc(t.id) + "\\">" + esc(TI.edit) + "</button> <button type=\\"button\\" data-del=\\"" + esc(t.id) + "\\">" + esc(TI.del) + "</button></td></tr>";
      }
      h += "</tbody></table>";
      box.innerHTML = h;
      box.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => startEdit(b.dataset.edit));
      box.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => delTrip(b.dataset.del));
    }

    function esc(s) {
      if (s==null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    function startEdit(id) {
      fetch("/api/travel-trips").then((r) => r.json()).then((trips) => {
        const t = trips.find((x) => x.id === id);
        if (!t) return;
        document.getElementById("edit_id").value = t.id;
        document.getElementById("purpose").value = t.purpose || "";
        document.getElementById("country_code").value = t.country_code || "";
        document.getElementById("destination").value = t.destination || "";
        document.getElementById("date_from").value = t.date_from || "";
        document.getElementById("date_to").value = t.date_to || "";
        document.getElementById("claim_type").value = t.claim_type || "meal_allowance_cz";
        document.getElementById("amount_total").value = t.amount_total != null ? t.amount_total : "";
        document.getElementById("currency").value = t.currency || "CZK";
        document.getElementById("notes").value = t.notes || "";
        document.getElementById("btn_cancel").hidden = false;
        document.getElementById("btn_save").textContent = TI.saveEdit;
        window.scrollTo(0, 0);
        refreshStravnePreview();
      });
    }

    function resetForm() {
      document.getElementById("edit_id").value = "";
      document.getElementById("f").reset();
      document.getElementById("currency").value = "CZK";
      document.getElementById("claim_type").value = "meal_allowance_cz";
      document.getElementById("btn_cancel").hidden = true;
      document.getElementById("btn_save").textContent = TI.saveNew;
      document.getElementById("stravne_preview").textContent = "";
    }

    document.getElementById("btn_cancel").onclick = resetForm;

    async function delTrip(id) {
      if (!confirm(TI.delQ)) return;
      const r = await fetch("/api/travel-trips/" + encodeURIComponent(id), { method: "DELETE" });
      if (!r.ok) { statusEl.textContent = TI.delErr; return; }
      statusEl.textContent = TI.deleted;
      resetForm();
      loadList();
    }

    document.getElementById("f").onsubmit = async (e) => {
      e.preventDefault();
      const editId = document.getElementById("edit_id").value;
      const body = {
        purpose: document.getElementById("purpose").value,
        country_code: document.getElementById("country_code").value,
        destination: document.getElementById("destination").value,
        date_from: document.getElementById("date_from").value,
        date_to: document.getElementById("date_to").value,
        claim_type: document.getElementById("claim_type").value,
        currency: document.getElementById("currency").value || "CZK",
        notes: document.getElementById("notes").value || null
      };
      const amt = document.getElementById("amount_total").value;
      body.amount_total = amt === "" ? null : Number(amt);
      let r;
      if (editId) {
        r = await fetch("/api/travel-trips/" + encodeURIComponent(editId), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      } else {
        r = await fetch("/api/travel-trips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      }
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        statusEl.textContent = TI.err + " " + r.status + " " + (j.detail || JSON.stringify(j));
        return;
      }
      statusEl.textContent = editId ? TI.saved : TI.added;
      resetForm();
      loadList();
    };

    wireStravne();
    loadList();
  </script>
</body>
</html>
"""
    )


def reminders_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    ri = reminders_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>"""
        + html.escape(L("rem.title"))
        + """</title>
  """
        + APP_SHELL_CSS
        + """
  <style>
    .travel-main > .hint { margin: 0 0 var(--gap-sm); max-width: 52rem; }
    .travel-main table {
      width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: var(--gap-sm);
      background: var(--card); border: 1px solid var(--border); border-radius: 4px; overflow: hidden;
    }
    .travel-main th, .travel-main td { text-align: left; padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    .travel-main th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; background: var(--surface); }
    .travel-main > h2 { margin: var(--gap) 0 var(--gap-sm); font-size: 0.82rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
    #rem_wrap { margin-top: var(--gap-sm); }
    #rem_asof { font-size: 0.88rem; color: var(--muted); margin-bottom: var(--gap); }
    .rem-badge { display: inline-block; background: var(--accent-soft); color: var(--accent); padding: 0.15rem 0.45rem; border-radius: 3px; font-size: 0.75rem; font-weight: 600; margin-left: 0.35rem; }
    .rem-badge.warn { background: rgba(180, 80, 40, 0.15); color: #c65a28; }
  </style>
</head>
<body>
  """
        + nav_html(lang, np)
        + """
  <main class="app-main travel-main">
  <h1>"""
        + html.escape(L("rem.h1"))
        + """</h1>
  <p class="hint">"""
        + L("rem.lead")
        + """</p>
  <p class="hint">"""
        + html.escape(L("rem.disclaimer"))
        + """</p>
  <p id="rem_asof"></p>
  <div id="rem_wrap">
  <h2>"""
        + html.escape(L("rem.section_kvd"))
        + """</h2>
  <p id="rem_kvd_note" class="hint"></p>
  <div id="rem_kvd"></div>
  <h2>"""
        + html.escape(L("rem.section_obl"))
        + """</h2>
  <p class="hint"><a href="/obligations" id="rem_obl_a"></a></p>
  <div id="rem_obl"></div>
  </div>
  </main>
  """
        + NAV_HIGHLIGHT_SCRIPT
        + """
  <script>
    const RI = """
        + ri
        + """;
    function esc(s) {
      const d = document.createElement("div");
      d.textContent = s == null ? "" : String(s);
      return d.innerHTML;
    }
    function fmtDateYmd(ymd) {
      if (!ymd || ymd.length < 10) return "";
      const p = ymd.slice(0, 10).split("-");
      const dt = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
      return dt.toLocaleDateString(RI.locale, { year: "numeric", month: "short", day: "numeric" });
    }
    function kindLab(k) { return esc((RI.kinds && RI.kinds[k]) || k || ""); }
    function stLab(s) {
      if (s === "past_due") return esc(RI.stPast);
      if (s === "due_today") return esc(RI.stToday);
      return esc(RI.stUp);
    }
    function renderKvd(rows) {
      if (!rows || !rows.length) return "<p class=\\"hint\\">" + esc(RI.kvdEmpty) + "</p>";
      let h = "<table><thead><tr>";
      h += "<th>" + esc(RI.thPeriod) + "</th><th>" + esc(RI.thRc) + "</th><th>" + esc(RI.thDue) + "</th><th>" + esc(RI.thState) + "</th>";
      h += "</tr></thead><tbody>";
      for (const r of rows) {
        const st = r.status || "";
        const badge = st === "past_due" ? " warn" : "";
        const n = r.rc_count != null ? String(r.rc_count) : "—";
        h += "<tr><td>" + esc(r.period_month || r.period_key || "") + "</td><td>" + esc(n) + "</td><td>" + fmtDateYmd(r.due_date) + "</td><td><span class=\\"rem-badge" + badge + "\\">" + stLab(st) + "</span></td></tr>";
      }
      h += "</tbody></table>";
      return h;
    }
    function renderObl(items, asOf) {
      if (!items || !items.length) return "<p class=\\"hint\\">" + esc(RI.oblEmpty) + "</p>";
      const nf = new Intl.NumberFormat(RI.locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
      let h = "<table><thead><tr>";
      h += "<th>" + esc(RI.thKind) + "</th><th>" + esc(RI.thTitle) + "</th><th>" + esc(RI.thAmt) + "</th><th>" + esc(RI.thDue) + "</th><th>" + esc(RI.thState) + "</th>";
      h += "</tr></thead><tbody>";
      for (const r of items) {
        const syn = r.synthetic ? "<span class=\\"rem-badge\\">" + esc(RI.badgeSynth) + "</span> " : "";
        const tit = syn + esc(r.title != null && r.title !== "" ? r.title : (r.period_month || "—"));
        let st = "";
        if (r.overdue) st = "<span class=\\"rem-badge warn\\">" + esc(RI.stPast) + "</span>";
        else if (asOf && r.due_date && String(r.due_date).slice(0, 10) === String(asOf).slice(0, 10)) st = "<span class=\\"rem-badge\\">" + esc(RI.stToday) + "</span>";
        else st = "<span class=\\"rem-badge\\">" + esc(RI.stUp) + "</span>";
        const amt = r.amount != null ? nf.format(Number(r.amount)) : "—";
        h += "<tr><td>" + kindLab(r.kind) + "</td><td>" + tit + "</td><td>" + esc(amt) + " " + esc(r.currency || "") + "</td><td>" + fmtDateYmd(r.due_date) + "</td><td>" + st + "</td></tr>";
      }
      h += "</tbody></table>";
      return h;
    }
    async function loadRem() {
      const r = await fetch("/api/reminders");
      const d = await r.json();
      document.getElementById("rem_asof").textContent = RI.asOf + ": " + fmtDateYmd(d.as_of_date || "");
      document.getElementById("rem_obl_a").textContent = RI.oblLink;
      const note = document.getElementById("rem_kvd_note");
      note.innerHTML = RI.kvdActive + " <a href=\\"/income\\">" + esc(RI.kvdRcLink) + "</a>";
      document.getElementById("rem_kvd").innerHTML = renderKvd(d.kvd_rows || []);
      document.getElementById("rem_obl").innerHTML = renderObl(d.obligation_reminders || [], d.as_of_date || "");
    }
    loadRem();
  </script>
</body>
</html>
"""
    )


def obligations_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    oi = obligations_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>"""
        + html.escape(L("obl.title"))
        + """</title>
  """
        + APP_SHELL_CSS
        + """
  <style>
    .travel-main > .hint { margin: 0 0 var(--gap-sm); max-width: 46rem; }
    .travel-main label { display: block; font-weight: 600; font-size: 0.88rem; line-height: 1.3; }
    .travel-main label input, .travel-main label select, .travel-main label textarea {
      display: block; width: 100%; margin-top: var(--gap-xs); box-sizing: border-box;
    }
    .travel-main textarea { min-height: 4rem; }
    .travel-main #obl_f > label { margin-top: var(--gap); }
    .travel-main #obl_f > label:first-of-type { margin-top: 0; }
    .travel-main #obl_f > .row2 { margin-top: var(--gap); }
    .travel-main .row2 {
      display: grid; grid-template-columns: 1fr 1fr; gap: var(--gap) var(--gap-sm); align-items: start;
    }
    @media (max-width: 520px) { .travel-main .row2 { grid-template-columns: 1fr; } }
    .travel-main .row2 label { margin-top: 0; }
    .travel-main #obl_f .btn-primary, .travel-main #obl_f > button:not(.btn-primary) { margin-top: var(--gap); }
    .travel-main button:not(.btn-primary) {
      margin-right: var(--gap-sm); padding: 0.5rem 0.85rem; cursor: pointer; border-radius: 3px;
      border: 1px solid var(--border); background: var(--surface-2); color: var(--text);
      font-family: inherit; font-size: 0.88rem;
    }
    .travel-main table {
      width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: var(--gap);
      background: var(--card); border: 1px solid var(--border); border-radius: 4px; overflow: hidden;
    }
    .travel-main th, .travel-main td { text-align: left; padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    .travel-main th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; background: var(--surface); }
    #obl_status { margin: var(--gap-sm) 0 0; font-size: 0.88rem; color: var(--muted); }
    .travel-main > h2 { margin: var(--gap) 0 var(--gap-sm); font-size: 0.82rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
    .travel-card { padding: var(--pad-card-y) var(--pad-card-x); margin-bottom: var(--gap); }
    #obl_overview .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: var(--gap-sm);
      margin-bottom: var(--gap);
      align-items: start;
    }
    #obl_overview .stat {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: var(--pad-card-y) var(--pad-card-x);
      box-sizing: border-box;
      width: 100%;
      max-width: 100%;
      aspect-ratio: 1 / 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      justify-content: center;
      overflow: auto;
    }
    #obl_overview .stat .label {
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      line-height: 1.2;
    }
    #obl_overview .stat .value { font-size: 1.28rem; font-weight: 650; margin-top: var(--gap-xs); line-height: 1.2; }
    #obl_overview .stat .stat-badges {
      margin-top: 0.4rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      align-items: center;
      line-height: 1.35;
    }
    #obl_overview .stat .stat-note {
      margin-top: 0.35rem;
      font-size: 0.65rem;
      color: var(--muted);
      text-transform: none;
      letter-spacing: 0;
      line-height: 1.25;
    }
    #obl_overview .badge {
      display: inline-block;
      background: var(--accent-soft);
      color: var(--accent);
      padding: 0.2rem 0.5rem;
      border-radius: 3px;
      font-size: 0.78rem;
      font-weight: 600;
      white-space: nowrap;
    }
  </style>
</head>
<body>
  """
        + nav_html(lang, np)
        + """
  <main class="app-main travel-main">
  <h1>"""
        + html.escape(L("obl.h1"))
        + """</h1>
  <p class="hint">"""
        + L("obl.lead")
        + """</p>
  <p class="hint">"""
        + html.escape(L("obl.json_path"))
        + """ <code>/data/obligations.json</code></p>
  <p class="hint">"""
        + L("obl.json_summary")
        + """</p>

  <h2>"""
        + html.escape(L("obl.meta_h2"))
        + """</h2>
  <div class="travel-card">
    <div class="row2">
      <label>"""
        + html.escape(L("obl.meta_osvc"))
        + """<input type="date" id="obl_m_osvc"/></label>
      <label>"""
        + html.escape(L("obl.meta_sickness"))
        + """<input type="date" id="obl_m_sick"/></label>
    </div>
    <div>
      <input type="checkbox" id="obl_m_vat"/>
      <label for="obl_m_vat">"""
        + html.escape(L("obl.meta_vat"))
        + """</label>
    </div>
    <label>"""
        + html.escape(L("obl.meta_vat_from"))
        + """<input type="date" id="obl_m_vat_from"/></label>
    <p class="btn-row"><button type="button" class="btn-primary" id="obl_m_save">"""
        + html.escape(L("obl.meta_save"))
        + """</button><span id="obl_m_status"></span></p>
  </div>

  <h2>"""
        + html.escape(L("obl.souhrn"))
        + """</h2>
  <div class="travel-card"><div id="obl_overview"><p class="hint">"""
        + html.escape(L("obl.load_list"))
        + """</p></div></div>

  <div class="travel-card">
  <form id="obl_f">
    <input type="hidden" id="obl_edit_id" value=""/>
    <label>"""
        + html.escape(L("obl.preset"))
        + """<select id="obl_preset">
      <option value="">"""
        + html.escape(L("obl.preset_none"))
        + """</option>
      <option value="health">"""
        + html.escape(L("obl.kind.health"))
        + """ (3306)</option>
      <option value="sickness">"""
        + html.escape(L("obl.kind.sickness"))
        + """ (243)</option>
      <option value="pension">"""
        + html.escape(L("obl.kind.pension"))
        + """ (5005)</option>
    </select></label>
    <label>"""
        + html.escape(L("obl.kind"))
        + """<select id="obl_kind">
      <option value="health">"""
        + html.escape(L("obl.kind.health"))
        + """</option>
      <option value="sickness">"""
        + html.escape(L("obl.kind.sickness"))
        + """</option>
      <option value="pension">"""
        + html.escape(L("obl.kind.pension"))
        + """</option>
      <option value="tax">"""
        + html.escape(L("obl.kind.tax"))
        + """</option>
      <option value="vat">"""
        + html.escape(L("obl.kind.vat"))
        + """</option>
      <option value="fine">"""
        + html.escape(L("obl.kind.fine"))
        + """</option>
      <option value="other">"""
        + html.escape(L("obl.kind.other"))
        + """</option>
    </select></label>
    <label>"""
        + html.escape(L("obl.title_lbl"))
        + """<input type="text" id="obl_title" placeholder=\""""
        + html.escape(L("obl.title_ph"))
        + """\"/></label>
    <div class="row2">
      <label>"""
        + html.escape(L("obl.amount"))
        + """<input type="number" step="any" id="obl_amount" required/></label>
      <label>"""
        + html.escape(L("obl.currency"))
        + """<input type="text" id="obl_currency" value="CZK" maxlength="3"/></label>
    </div>
    <div class="row2">
      <label>"""
        + html.escape(L("obl.due"))
        + """<input type="date" id="obl_due" required/></label>
      <label>"""
        + html.escape(L("obl.paid"))
        + """<input type="date" id="obl_paid"/></label>
    </div>
    <label>"""
        + html.escape(L("obl.period_month"))
        + """<input type="text" id="obl_period" inputmode="text" autocomplete="off" placeholder=\""""
        + html.escape(L("obl.period_ph"))
        + """\"/></label>
    <label>"""
        + html.escape(L("obl.notes"))
        + """<textarea id="obl_notes"></textarea></label>
    <button type="submit" class="btn-primary" id="obl_btn_save">"""
        + html.escape(L("obl.save"))
        + """</button>
    <button type="button" id="obl_btn_cancel" hidden>"""
        + html.escape(L("obl.cancel"))
        + """</button>
  </form>
  </div>
  <p id="obl_status"></p>

  <h2>"""
        + html.escape(L("obl.list"))
        + """</h2>
  <div class="btn-row">
    <label>"""
        + html.escape(L("obl.filter"))
        + """
      <select id="obl_filter">
        <option value="all">"""
        + html.escape(L("obl.f_all"))
        + """</option>
        <option value="unpaid">"""
        + html.escape(L("obl.f_unpaid"))
        + """</option>
        <option value="paid">"""
        + html.escape(L("obl.f_paid"))
        + """</option>
      </select>
    </label>
  </div>
  <div id="obl_summary"></div>
  <div id="obl_list"><p class="hint">"""
        + html.escape(L("obl.load_list"))
        + """</p></div>
  </main>
  """
        + NAV_HIGHLIGHT_SCRIPT
        + """
  <script>
    const OI = """
        + oi
        + """;
    const oblStatus = document.getElementById("obl_status");
    const nf = new Intl.NumberFormat(OI.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    let oblPresets = [];
    let oblAll = [];
    let oblUnpaid = [];
    let oblFilter = "all";

    function esc(s) {
      if (s == null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }

    function todayYmd() {
      const d = new Date();
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return y + "-" + m + "-" + day;
    }

    function rowBadge(e) {
      if (e.paid_date) return esc(OI.badgePaid);
      if (e.due_date && e.due_date < todayYmd()) return esc(OI.badgeOverdue);
      return esc(OI.badgeUnpaid);
    }

    function paidControlsHtml(paidDate, idxAttr, idx) {
      const pd = paidDate || "";
      let h = "<label><input type=\\"checkbox\\" class=\\"obl-paid-cb\\" " + idxAttr + "=\\"" + esc(String(idx)) + "\\"";
      if (pd) h += " checked";
      h += "/> " + esc(OI.markPaid) + "</label> ";
      h += "<input type=\\"date\\" class=\\"obl-paid-d\\" " + idxAttr + "=\\"" + esc(String(idx)) + "\\" value=\\"" + esc(pd) + "\\"/>";
      return h;
    }

    function amtControlHtml(amount, currency, idxAttr, idx) {
      const a = amount != null ? String(amount) : "";
      return "<input type=\\"number\\" step=\\"any\\" class=\\"obl-amt\\" " + idxAttr + "=\\"" + esc(String(idx)) + "\\" value=\\"" + esc(a) + "\\"/> " + esc(currency || "");
    }

    function rowAmount(tr, fallback) {
      const inp = tr ? tr.querySelector(".obl-amt") : null;
      if (!inp || inp.value === "") return Number(fallback != null ? fallback : 0);
      const n = Number(inp.value);
      return Number.isFinite(n) ? n : Number(fallback != null ? fallback : 0);
    }

    async function persistEntry(body, editId, okMsg) {
      let r;
      if (editId) {
        r = await fetch("/api/obligations/" + encodeURIComponent(editId), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      } else {
        r = await fetch("/api/obligations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      }
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        oblStatus.textContent = OI.err + " " + r.status + " " + (j.detail || JSON.stringify(j));
        return false;
      }
      oblStatus.textContent = okMsg || OI.saved;
      await loadObl();
      return true;
    }

    function baseBody(src, amount, paidDate) {
      return {
        kind: src.kind || "other",
        title: src.title || null,
        amount: Number(amount),
        currency: src.currency || "CZK",
        due_date: src.due_date || todayYmd(),
        period_month: src.period_month || null,
        notes: src.notes || null,
        paid_date: paidDate
      };
    }

    async function saveEntryPaid(entry, paidDate, tr) {
      if (!entry || !entry.id) return;
      const amt = rowAmount(tr, entry.amount);
      const msg = paidDate ? OI.paidSaved : OI.paidCleared;
      await persistEntry(baseBody(entry, amt, paidDate || null), entry.id, msg);
    }

    async function saveEntryAmount(entry, tr) {
      if (!entry || !entry.id) return;
      const amt = rowAmount(tr, entry.amount);
      await persistEntry(baseBody(entry, amt, entry.paid_date || null), entry.id, OI.saved);
    }

    async function saveUnpaidPaid(it, paidDate, tr) {
      if (!it) return;
      const amt = rowAmount(tr, it.amount);
      const pd = paidDate || null;
      if (!pd) {
        if (it.id) {
          const e = oblAll.find((x) => x.id === it.id) || it;
          await persistEntry(baseBody(e, amt, null), it.id, OI.paidCleared);
        }
        return;
      }
      if (it.id) {
        const e = oblAll.find((x) => x.id === it.id) || it;
        await persistEntry(baseBody(e, amt, pd), it.id, OI.paidSaved);
        return;
      }
      await persistEntry(baseBody(it, amt, pd), null, OI.paidSaved);
    }

    async function saveUnpaidAmount(it, tr) {
      if (!it) return;
      const amt = rowAmount(tr, it.amount);
      if (it.id) {
        const e = oblAll.find((x) => x.id === it.id) || it;
        await persistEntry(baseBody(e, amt, e.paid_date || null), it.id, OI.saved);
        return;
      }
      await persistEntry(baseBody(it, amt, null), null, OI.saved);
    }

    function wireRowControls(root, items, onPaid, onAmount) {
      root.querySelectorAll(".obl-paid-cb").forEach((cb) => {
        cb.onchange = () => {
          const idx = Number(cb.getAttribute("data-obl-i") || cb.getAttribute("data-obl-u"));
          const it = items[idx];
          const tr = cb.closest("tr");
          const dt = tr ? tr.querySelector(".obl-paid-d") : null;
          if (cb.checked) {
            const v = (dt && dt.value) ? dt.value : todayYmd();
            if (dt) dt.value = v;
            onPaid(it, v, tr);
          } else {
            if (dt) dt.value = "";
            onPaid(it, null, tr);
          }
        };
      });
      root.querySelectorAll(".obl-paid-d").forEach((dt) => {
        dt.onchange = () => {
          const idx = Number(dt.getAttribute("data-obl-i") || dt.getAttribute("data-obl-u"));
          const it = items[idx];
          const tr = dt.closest("tr");
          const cb = tr ? tr.querySelector(".obl-paid-cb") : null;
          const v = dt.value || null;
          if (cb) cb.checked = !!v;
          onPaid(it, v, tr);
        };
      });
      root.querySelectorAll(".obl-amt").forEach((inp) => {
        inp.onchange = () => {
          const idx = Number(inp.getAttribute("data-obl-i") || inp.getAttribute("data-obl-u"));
          const it = items[idx];
          onAmount(it, inp.closest("tr"));
        };
      });
    }

    function renderOverview(summary) {
      const box = document.getElementById("obl_overview");
      if (!summary) {
        box.innerHTML = "";
        oblUnpaid = [];
        return;
      }
      const p = summary.paid || {};
      const u = summary.unpaid || {};
      const cts = summary.counts || {};
      const paidCzk = nf.format(p.total_paid_czk || 0);
      const unpaidCzk = nf.format(u.total_unpaid_czk || 0);
      const bd = [];
      bd.push("<span class=\\"badge\\">" + esc(OI.badgePaid) + " " + String(cts.paid != null ? cts.paid : 0) + "</span>");
      bd.push("<span class=\\"badge\\">" + esc(OI.badgeUnpaid) + " " + String(cts.unpaid != null ? cts.unpaid : 0) + "</span>");
      if ((cts.overdue_unpaid != null ? cts.overdue_unpaid : 0) > 0) {
        bd.push("<span class=\\"badge\\">" + esc(OI.badgeOverdue) + " " + String(cts.overdue_unpaid) + "</span>");
      }
      let h = "<div class=\\"summary-grid\\">";
      h += "<div class=\\"stat\\"><div class=\\"label\\">" + esc(OI.ovPaidTotal) + "</div><div class=\\"value\\">" + esc(paidCzk) + "</div></div>";
      h += "<div class=\\"stat\\"><div class=\\"label\\">" + esc(OI.ovUnpaidTotal) + "</div><div class=\\"value\\">" + esc(unpaidCzk) + "</div></div>";
      h += "<div class=\\"stat\\"><div class=\\"label\\">" + esc(OI.ovStatEntries) + "</div><div class=\\"value\\">" + esc(String(cts.entries_total != null ? cts.entries_total : 0)) + "</div><div class=\\"stat-badges\\">" + bd.join("") + "</div></div>";
      h += "</div>";
      if (summary.as_of_date) {
        h += "<p class=\\"hint\\"><small>" + esc(OI.ovAsOf) + " " + esc(summary.as_of_date) + "</small></p>";
      }
      const items = u.items || [];
      oblUnpaid = items;
      if (!items.length) {
        h += "<p class=\\"hint\\">" + esc(OI.ovNoneUnpaid) + "</p>";
      } else {
        h += "<p><strong>" + esc(OI.ovUnpaidTitle) + "</strong></p><table><thead><tr><th>" + esc(OI.thSynthetic) + "</th><th>" + esc(OI.thKind) + "</th><th>" + esc(OI.thTitle) + "</th><th>" + esc(OI.thAmt) + "</th><th>" + esc(OI.thDue) + "</th><th>" + esc(OI.thPaid) + "</th></tr></thead><tbody>";
        for (let i = 0; i < items.length; i++) {
          const it = items[i];
          const klab = OI.kinds[it.kind] || it.kind;
          const src = it.synthetic ? esc(OI.syntheticAuto) : esc(OI.syntheticYes);
          h += "<tr><td>" + src + "</td><td>" + esc(klab) + "</td><td>" + esc(it.title || "—") + "</td><td>" + amtControlHtml(it.amount, it.currency, "data-obl-u", i) + "</td><td>" + esc(it.due_date || "") + "</td><td>" + paidControlsHtml(null, "data-obl-u", i) + "</td></tr>";
        }
        h += "</tbody></table>";
      }
      box.innerHTML = h;
      wireRowControls(box, oblUnpaid, saveUnpaidPaid, saveUnpaidAmount);
    }

    function renderSummary(summary) {
      const box = document.getElementById("obl_summary");
      const paid = summary && summary.paid;
      const byccy = (paid && paid.by_kind_by_currency) || (summary && summary.by_kind_by_currency) || {};
      const keys = Object.keys(byccy).sort();
      if (!keys.length) {
        box.innerHTML = "<p class=\\"hint\\">" + esc(OI.ovPaidBreakdown) + ": " + esc(OI.sumEmpty) + "</p>";
        return;
      }
      let h = "<p><strong>" + esc(OI.ovPaidBreakdown) + "</strong></p><table><thead><tr><th>" + esc(OI.thKind) + "</th><th>" + esc(OI.thAmt) + "</th></tr></thead><tbody>";
      for (const ccy of keys) {
        const kinds = byccy[ccy] || {};
        for (const k of Object.keys(kinds).sort()) {
          const lab = OI.kinds[k] || k;
          h += "<tr><td>" + esc(lab) + " (" + esc(ccy) + ")</td><td>" + esc(nf.format(kinds[k])) + "</td></tr>";
        }
      }
      h += "</tbody></table>";
      box.innerHTML = h;
    }

    function passesFilter(e) {
      if (oblFilter === "all") return true;
      const paid = !!e.paid_date;
      if (oblFilter === "paid") return paid;
      return !paid;
    }

    function renderTable() {
      const box = document.getElementById("obl_list");
      const rows = oblAll.filter(passesFilter);
      if (!rows.length) {
        box.innerHTML = "<p class=\\"hint\\">" + esc(OI.emptyList) + "</p>";
        return;
      }
      let h = "<table><thead><tr><th></th><th>" + esc(OI.thKind) + "</th><th>" + esc(OI.thTitle) + "</th><th>" + esc(OI.thAmt) + "</th><th>" + esc(OI.thPeriod) + "</th><th>" + esc(OI.thDue) + "</th><th>" + esc(OI.thPaid) + "</th><th>" + esc(OI.thNotes) + "</th><th></th></tr></thead><tbody>";
      for (let i = 0; i < rows.length; i++) {
        const e = rows[i];
        const klab = OI.kinds[e.kind] || e.kind;
        const tit = esc(e.title || "");
        const notes = esc((e.notes || "").slice(0, 80)) + ((e.notes || "").length > 80 ? "…" : "");
        const pm = e.period_month ? esc(e.period_month) : "—";
        h += "<tr><td>" + rowBadge(e) + "</td><td>" + esc(klab) + "</td><td>" + tit + "</td><td>" + amtControlHtml(e.amount, e.currency, "data-obl-i", i) + "</td><td>" + pm + "</td><td>" + esc(e.due_date || "") + "</td><td>" + paidControlsHtml(e.paid_date, "data-obl-i", i) + "</td><td><small>" + notes + "</small></td><td><button type=\\"button\\" data-obl-edit=\\"" + esc(e.id) + "\\">" + esc(OI.edit) + "</button> <button type=\\"button\\" data-obl-del=\\"" + esc(e.id) + "\\">" + esc(OI.del) + "</button></td></tr>";
      }
      h += "</tbody></table>";
      box.innerHTML = h;
      box.querySelectorAll("[data-obl-edit]").forEach((b) => { b.onclick = () => startOblEdit(b.dataset.oblEdit); });
      box.querySelectorAll("[data-obl-del]").forEach((b) => { b.onclick = () => delObl(b.dataset.oblDel); });
      wireRowControls(box, rows, saveEntryPaid, saveEntryAmount);
    }

    function applyOblMeta(m) {
      document.getElementById("obl_m_osvc").value = (m && m.osvc_since) ? m.osvc_since : "";
      document.getElementById("obl_m_sick").value = (m && m.sickness_from) ? m.sickness_from : "";
      document.getElementById("obl_m_vat").checked = !!(m && m.vat_identified);
      document.getElementById("obl_m_vat_from").value = (m && m.vat_identified_from) ? m.vat_identified_from : "";
    }

    document.getElementById("obl_m_save").onclick = async () => {
      const st = document.getElementById("obl_m_status");
      st.textContent = "";
      const vatOn = document.getElementById("obl_m_vat").checked;
      const body = {
        osvc_since: document.getElementById("obl_m_osvc").value || null,
        sickness_from: document.getElementById("obl_m_sick").value || null,
        vat_identified: vatOn,
        vat_identified_from: vatOn ? (document.getElementById("obl_m_vat_from").value || null) : null
      };
      const r = await fetch("/api/obligations/meta", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        st.textContent = OI.metaErr + " " + r.status + " " + (j.detail || JSON.stringify(j));
        return;
      }
      applyOblMeta(j);
      await loadObl();
      st.textContent = OI.metaSaved;
    };

    async function loadObl() {
      const r = await fetch("/api/obligations");
      const data = await r.json();
      oblPresets = data.presets || [];
      applyOblMeta(data.meta);
      oblAll = (data.entries || []).slice().sort((a, b) => String(b.due_date).localeCompare(String(a.due_date)));
      renderOverview(data.summary);
      renderSummary(data.summary);
      renderTable();
    }

    document.getElementById("obl_filter").onchange = function() {
      oblFilter = this.value || "all";
      renderTable();
    };

    document.getElementById("obl_preset").onchange = function() {
      const v = this.value;
      if (!v) return;
      const p = oblPresets.find((x) => x.kind === v);
      if (p) {
        document.getElementById("obl_kind").value = p.kind;
        document.getElementById("obl_amount").value = p.default_amount != null ? p.default_amount : "";
        document.getElementById("obl_currency").value = p.currency || "CZK";
      }
      this.value = "";
    };

    function resetOblForm() {
      document.getElementById("obl_edit_id").value = "";
      document.getElementById("obl_f").reset();
      document.getElementById("obl_currency").value = "CZK";
      document.getElementById("obl_kind").value = "health";
      document.getElementById("obl_period").value = "";
      document.getElementById("obl_btn_cancel").hidden = true;
      document.getElementById("obl_btn_save").textContent = OI.saveNew;
    }

    document.getElementById("obl_btn_cancel").onclick = resetOblForm;

    function startOblEdit(id) {
      const e = oblAll.find((x) => x.id === id);
      if (!e) return;
      document.getElementById("obl_edit_id").value = e.id;
      document.getElementById("obl_kind").value = e.kind || "other";
      document.getElementById("obl_title").value = e.title || "";
      document.getElementById("obl_amount").value = e.amount != null ? e.amount : "";
      document.getElementById("obl_currency").value = e.currency || "CZK";
      document.getElementById("obl_due").value = e.due_date || "";
      document.getElementById("obl_paid").value = e.paid_date || "";
      document.getElementById("obl_period").value = e.period_month || "";
      document.getElementById("obl_notes").value = e.notes || "";
      document.getElementById("obl_btn_cancel").hidden = false;
      document.getElementById("obl_btn_save").textContent = OI.saveEdit;
      window.scrollTo(0, 0);
    }

    async function delObl(id) {
      if (!confirm(OI.delQ)) return;
      const r = await fetch("/api/obligations/" + encodeURIComponent(id), { method: "DELETE" });
      if (!r.ok) { oblStatus.textContent = OI.delErr; return; }
      oblStatus.textContent = OI.deleted;
      resetOblForm();
      loadObl();
    }

    document.getElementById("obl_f").onsubmit = async (e) => {
      e.preventDefault();
      const editId = document.getElementById("obl_edit_id").value;
      const pm = document.getElementById("obl_period").value.trim();
      const body = {
        kind: document.getElementById("obl_kind").value,
        title: document.getElementById("obl_title").value.trim() || null,
        amount: Number(document.getElementById("obl_amount").value),
        currency: document.getElementById("obl_currency").value || "CZK",
        due_date: document.getElementById("obl_due").value,
        notes: document.getElementById("obl_notes").value.trim() || null,
        period_month: pm || null
      };
      const pd = document.getElementById("obl_paid").value;
      body.paid_date = pd || null;
      let r;
      if (editId) {
        r = await fetch("/api/obligations/" + encodeURIComponent(editId), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      } else {
        r = await fetch("/api/obligations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      }
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        oblStatus.textContent = OI.err + " " + r.status + " " + (j.detail || JSON.stringify(j));
        return;
      }
      oblStatus.textContent = editId ? OI.saved : OI.added;
      resetOblForm();
      loadObl();
    };

    loadObl();
  </script>
</body>
</html>
"""
    )


def duplicates_html(lang: str, np: str) -> str:
    L = lambda k: tr(lang, k)
    ha = html_lang_attr(lang)
    di = dup_i18n_json(lang)
    return (
        """<!DOCTYPE html>
<html lang="""
        + ha
        + """>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>"""
        + html.escape(L("dup.title"))
        + """</title>
    """
        + APP_SHELL_CSS
    + """
    <style>
      #groups section { background: var(--card); border: 1px solid var(--border); border-radius: 4px; padding: var(--pad-card-y) var(--pad-card-x); margin-bottom: var(--gap); }
      #groups h2 { font-size: 0.95rem; margin: 0 0 var(--gap-sm); line-height: 1.3; }
      #groups table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
      #groups th, #groups td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
      #groups th { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; background: var(--surface); }
      #groups img { max-width: 10rem; max-height: 8rem; border-radius: 4px; border: 1px solid var(--border); }
      #groups + .btn-row { margin-top: var(--gap); }
    </style>
  </head>
  <body>
    """
        + nav_html(lang, np)
        + """
    <main class="app-main">
    <h1>"""
        + html.escape(L("dup.h1"))
        + """</h1>
    <p class="lead">"""
        + L("dup.lead")
        + """</p>
    <p id="empty" class="lead" hidden>"""
        + html.escape(L("dup.empty"))
        + """</p>
    <div id="groups"></div>
    <div class="btn-row"><button type="button" class="btn-primary" id="apply" hidden>"""
        + html.escape(L("dup.apply"))
        + """</button><span id="status"></span></div>
    </main>
    """
        + NAV_HIGHLIGHT_SCRIPT
        + """
    <script>
      const DI = """
        + di
        + """;
      async function loadGroups() {
        const r = await fetch("/api/duplicates");
        const groups = await r.json();
        const box = document.getElementById("groups");
        const empty = document.getElementById("empty");
        const apply = document.getElementById("apply");
        box.innerHTML = "";
        if (!groups.length) {
          empty.hidden = false;
          apply.hidden = true;
          return;
        }
        empty.hidden = true;
        apply.hidden = false;
        for (const g of groups) {
          const sec = document.createElement("section");
          const h = document.createElement("h2");
          h.textContent = g.date + " · " + g.total + " " + g.currency + " (" + g.items.length + " " + DI.rows + ")";
          sec.appendChild(h);
          const tbl = document.createElement("table");
          const thead = document.createElement("thead");
          thead.innerHTML = "<tr><th>" + DI.thRm + "</th><th>" + DI.thFile + "</th><th>" + DI.thBucket + "</th><th>" + DI.thMerch + "</th><th>" + DI.thCat + "</th><th>" + DI.thPrev + "</th></tr>";
          tbl.appendChild(thead);
          const tb = document.createElement("tbody");
          for (const it of g.items) {
            const rec = it.receipt;
            const tr = document.createElement("tr");
            const td0 = document.createElement("td");
            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.dataset.rid = it.id;
            td0.appendChild(cb);
            tr.appendChild(td0);
            const td1 = document.createElement("td");
            td1.textContent = rec.source_file || "";
            tr.appendChild(td1);
            const td2 = document.createElement("td");
            td2.textContent = it.bucket_file || "";
            tr.appendChild(td2);
            const td3 = document.createElement("td");
            td3.textContent = rec.merchant_hint || "";
            tr.appendChild(td3);
            const td4 = document.createElement("td");
            td4.textContent = rec.category || "";
            tr.appendChild(td4);
            const td5 = document.createElement("td");
            const img = document.createElement("img");
            img.alt = "";
            img.src = "/api/receipts/" + encodeURIComponent(it.id) + "/file";
            img.onerror = () => { img.replaceWith(document.createTextNode("—")); };
            td5.appendChild(img);
            tr.appendChild(td5);
            tb.appendChild(tr);
          }
          tbl.appendChild(tb);
          sec.appendChild(tbl);
          box.appendChild(sec);
        }
      }

      document.getElementById("apply").onclick = async () => {
        const remove_ids = [];
        document.querySelectorAll("input[type=checkbox][data-rid]:checked").forEach((cb) => {
          remove_ids.push(cb.dataset.rid);
        });
        if (!remove_ids.length) {
          alert(DI.alertNone);
          return;
        }
        if (!confirm(DI.confirm.replace("{n}", String(remove_ids.length)))) return;
        const st = document.getElementById("status");
        st.textContent = "…";
        const res = await fetch("/api/duplicates/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ remove_ids: remove_ids })
        });
        const j = await res.json().catch(() => ({}));
        if (!res.ok) {
          st.textContent = DI.err + " " + res.status + " " + JSON.stringify(j);
          return;
        }
        st.textContent = JSON.stringify(j);
        loadGroups();
      };
      loadGroups();
    </script>
  </body>
</html>
"""
)


@app.get("/incomplete", response_class=HTMLResponse)
def incomplete_page(request: Request) -> str:
    lang = get_lang(request)
    return incomplete_html(lang, _np(request))


@app.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(request: Request) -> str:
    lang = get_lang(request)
    return duplicates_html(lang, _np(request))


@app.get("/travel", response_class=HTMLResponse)
def travel_page(request: Request) -> str:
    lang = get_lang(request)
    return travel_html(lang, _np(request))


@app.get("/income", response_class=HTMLResponse)
def income_page(request: Request) -> str:
    lang = get_lang(request)
    return income_html(lang, _np(request))


@app.get("/obligations", response_class=HTMLResponse)
def obligations_page(request: Request) -> str:
    lang = get_lang(request)
    return obligations_html(lang, _np(request))


@app.get("/reminders", response_class=HTMLResponse)
def reminders_page(request: Request) -> str:
    lang = get_lang(request)
    return reminders_html(lang, _np(request))


@app.get("/overview", response_class=HTMLResponse)
def overview_page(request: Request) -> str:
    lang = get_lang(request)
    return overview_html(lang, _np(request))


@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request) -> str:
    lang = get_lang(request)
    return categories_html(lang, _np(request))


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request) -> str:
    lang = get_lang(request)
    return search_html(lang, _np(request))


@app.get("/tax-reference", response_class=HTMLResponse)
def tax_reference_page(request: Request) -> str:
    lang = get_lang(request)
    return tax_html(lang, _np(request))


@app.get("/tax-rc", response_class=HTMLResponse)
def tax_rc_page(request: Request) -> str:
    lang = get_lang(request)
    return tax_rc_html(lang, _np(request))


@app.get("/api/tax-rc-review")
def api_tax_rc_review(scan_pdf: bool = True) -> Dict[str, Any]:
    return build_tax_rc_review(OUTPUT, ROOT, scan_pdf=scan_pdf)


@app.put("/api/tax-rc-review/dismiss")
def api_tax_rc_dismiss(body: TaxRcDismissBody) -> Dict[str, Any]:
    try:
        set_dismissed_receipt_id(OUTPUT, body.receipt_id, body.dismissed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return build_tax_rc_review(OUTPUT, ROOT, scan_pdf=True)


@app.get("/api/tax-categories")
def api_tax_categories() -> Dict[str, Any]:
    return load_tax_categories()


@app.get("/api/landing-notes")
def api_landing_notes_get() -> Dict[str, Any]:
    return get_notes(OUTPUT)


@app.get("/api/output-month-files")
def api_output_month_files() -> List[str]:
    names: List[str] = []
    start = date.today().replace(day=1)
    for i in range(18):
        d = start - relativedelta(months=i)
        ym = f"{d.year}-{d.month:02d}.json"
        if (OUTPUT / ym).is_file():
            names.append(ym)
    if (OUTPUT / "unknown.json").is_file():
        names.append("unknown.json")
    return names


@app.put("/api/landing-notes")
def api_landing_notes_put(body: LandingNotesBody) -> Dict[str, Any]:
    return set_notes(OUTPUT, body.text)


@app.get("/api/incomplete")
def api_incomplete() -> list:
    return list_incomplete_receipts(OUTPUT)


@app.get("/api/uncategorized")
def api_uncategorized() -> list:
    return list_uncategorized_receipts(OUTPUT)


@app.get("/api/search")
def api_search(q: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    return search_receipts(OUTPUT, q, limit=limit)


@app.get("/api/merchant-rules")
def api_merchant_rules() -> Dict[str, Any]:
    return list_rules_public(OUTPUT)


@app.post("/api/merchant-rules/apply")
def api_merchant_rules_apply() -> Dict[str, Any]:
    return apply_rules_to_uncategorized(OUTPUT)


@app.get("/api/duplicates")
def api_duplicates() -> list:
    return find_duplicate_receipt_groups(OUTPUT)


@app.get("/api/travel-trips")
def api_travel_list() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in list_trips(OUTPUT):
        row = dict(t)
        eff = effective_trip_amounts(t)
        row["amount_display"] = eff["amount_total"]
        row["currency_display"] = eff["currency"]
        row["is_stravne_estimate"] = eff["from_stravne_table"]
        if eff.get("stravne_detail_cs"):
            row["stravne_detail_cs"] = eff["stravne_detail_cs"]
        out.append(row)
    return out


@app.get("/api/stravne-suggest")
def api_stravne_suggest(
    country_code: str = "",
    date_from: str = "",
    date_to: str = "",
    claim_type: str = "meal_allowance_cz",
) -> Dict[str, Any]:
    meta = load_stravne_meta()
    if not date_from or not date_to:
        return {"ok": False, "reason": "missing_dates", "meta": meta}
    try:
        sug = suggest_foreign_meal_allowance(
            country_code, date_from, date_to, claim_type
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid dates") from None
    if not sug:
        return {"ok": False, "reason": "no_rate", "meta": meta}
    return {"ok": True, **sug, "meta": meta}


@app.post("/api/travel-trips")
def api_travel_create(body: TripCreateBody) -> Dict[str, Any]:
    try:
        return add_trip(OUTPUT, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.put("/api/travel-trips/{tid}")
def api_travel_update(tid: str, body: TripPatchBody) -> Dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        return update_trip(OUTPUT, tid, patch)
    except KeyError:
        raise HTTPException(status_code=404, detail="trip not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.delete("/api/travel-trips/{tid}")
def api_travel_delete(tid: str) -> dict:
    try:
        delete_trip(OUTPUT, tid)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="trip not found") from None


@app.get("/api/obligations")
def api_obligations_list() -> Dict[str, Any]:
    entries = list_obl_entries(OUTPUT)
    summary = build_obl_summary(OUTPUT)
    persist_obl_summary(OUTPUT, summary)
    return {
        "meta": get_obl_meta(OUTPUT),
        "entries": entries,
        "presets": list_obl_presets(),
        "summary": summary,
    }


@app.put("/api/obligations/meta")
def api_obligations_meta(body: ObligationsMetaBody) -> Dict[str, Any]:
    try:
        return set_obl_meta_fields(
            OUTPUT,
            body.osvc_since,
            body.sickness_from,
            body.vat_identified,
            body.vat_identified_from,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.post("/api/obligations")
def api_obligations_create(body: ObligationCreateBody) -> Dict[str, Any]:
    try:
        return add_obl_entry(OUTPUT, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.put("/api/obligations/{oid}")
def api_obligations_update(oid: str, body: ObligationCreateBody) -> Dict[str, Any]:
    patch = body.model_dump()
    try:
        return update_obl_entry(OUTPUT, oid, patch)
    except KeyError:
        raise HTTPException(status_code=404, detail="obligation not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.delete("/api/obligations/{oid}")
def api_obligations_delete(oid: str) -> dict:
    try:
        delete_obl_entry(OUTPUT, oid)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="obligation not found") from None


@app.get("/api/overview")
def api_overview() -> Dict[str, Any]:
    return build_overview(OUTPUT)


@app.get("/api/reminders")
def api_reminders() -> Dict[str, Any]:
    return build_reminder_overview(OUTPUT, ROOT)


@app.get("/api/income-invoices")
def api_income_invoices() -> Dict[str, Any]:
    data = list_income_rows(OUTPUT)
    rows = data.get("rows") or []
    exp_by_m = expense_czk_totals_by_month(OUTPUT)
    data["monthly_approx"] = build_monthly_approx_summary(rows, exp_by_m)
    selected = [r for r in rows if r.get("in_approx_selected")]
    data["monthly_approx_selected"] = build_monthly_approx_summary(selected, exp_by_m)
    return data


@app.put("/api/income-invoices/dir")
def api_income_dir(body: IncomeDirBody) -> Dict[str, Any]:
    try:
        path = set_invoices_dir(OUTPUT, body.invoices_dir.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True, "invoices_dir": path}


@app.put("/api/income-invoices/{iid}")
def api_income_row_patch(iid: str, body: IncomeRowPatchBody) -> Dict[str, Any]:
    kw: Dict[str, Any] = {}
    if "client_dic" in body.model_fields_set:
        kw["client_dic"] = body.client_dic
    if "client_vat" in body.model_fields_set:
        kw["client_vat"] = body.client_vat
    if "in_approx_selected" in body.model_fields_set:
        kw["in_approx_selected"] = body.in_approx_selected
    return patch_row(
        OUTPUT, iid, body.paid, body.paid_month, body.payment_date, **kw
    )


@app.get("/api/income-invoices/{iid}/file")
def api_income_file(iid: str):
    path = resolve_path_for_id(OUTPUT, iid)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="invoice file not found")
    return FileResponse(
        path,
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@app.post("/api/duplicates/remove")
def api_duplicates_remove(body: DuplicateRemoveBody) -> dict:
    if not body.remove_ids:
        raise HTTPException(status_code=400, detail="remove_ids required")
    return remove_receipts_by_ids(
        OUTPUT,
        OUTPUT / "_ledger.json",
        list(dict.fromkeys(body.remove_ids)),
    )


@app.post("/api/receipts/refresh-vat")
def api_refresh_vat(body: RefreshVatBody = RefreshVatBody()) -> Dict[str, Any]:
    return refresh_vat_from_source_files(OUTPUT, ROOT, force=body.force)


@app.put("/api/receipts/{rid}")
def api_put_receipt(rid: str, body: ReceiptPatchBody) -> dict:
    patch: Dict[str, Any] = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        return apply_receipt_update(
            OUTPUT,
            OUTPUT / "_ledger.json",
            ROOT,
            rid,
            patch,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="receipt not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.get("/api/receipts/{rid}/file")
def api_receipt_file(rid: str):
    found = find_receipt(OUTPUT, rid)
    if not found:
        raise HTTPException(status_code=404, detail="receipt not found")
    _path, _data, idx = found
    rec = _data["receipts"][idx]
    rel = rec.get("source_rel") or rec.get("source_file")
    if not rel:
        raise HTTPException(status_code=404, detail="no file path")
    try:
        fpath = safe_inbox_file(ROOT, str(rel))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid path") from None
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="file not on disk")
    media_type, _ = mimetypes.guess_type(str(fpath))
    if not media_type:
        media_type = "application/octet-stream"
    return FileResponse(
        fpath,
        media_type=media_type,
        content_disposition_type="inline",
    )


@app.post("/api/process")
def api_process() -> dict:
    summary = process_inbox(ROOT, OUTPUT, ROOT / "_notReadable", recursive=False)
    return summary


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
