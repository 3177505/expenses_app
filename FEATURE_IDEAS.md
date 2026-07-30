# Feature ideas

## Income — second monthly approx overview (filtered invoices)

**Page:** `/income`  
**Date noted:** 2026-07-30  
**Status:** built

### What exists

On the income page there is **Měsíční přibližný přehled (příjem − výdaje)**:
- paid income in CZK is spread evenly across all months in the activity range
- real monthly expenses are subtracted
- uses **all** paid invoices

### What was added

A **second** overview with the same math, based only on invoices marked in the **2. přehled** column.

### Behaviour

- Flag per invoice: `in_approx_selected`, stored in `income_invoices.json` → `by_id`.
- Checkbox column in the invoice table (opt-in).
- Second block: **Měsíční přibližný přehled — vybrané faktury (příjem − výdaje)**.
- First overview unchanged (all paid invoices).
- Expenses in the second overview are still full real monthly expenses.
