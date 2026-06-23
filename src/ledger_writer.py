import os
from datetime import date, datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, numbers
from openpyxl.chart import BarChart, Reference
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
BODY_FONT = Font(name="Arial", size=10)
CURRENCY_FMT = '_($* #,##0.00_);_($* (#,##0.00);_($* "-"??_);_(@_)'
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# 15-color deterministic palette (light backgrounds)
_PALETTE = [
    "FFF2CC", "D9EAD3", "CFE2F3", "F4CCCC", "EAD1DC",
    "D9D2E9", "FCE5CD", "D0E4F1", "E6F4EA", "FFF3E0",
    "F3E5F5", "E8F5E9", "FFF8E1", "E3F2FD", "FCE4EC",
]


def _category_fill(category: str) -> PatternFill:
    idx = hash(category or "") % len(_PALETTE)
    return PatternFill("solid", fgColor=_PALETTE[idx])


def _header_row(ws, columns: list, row: int = 1):
    for col, title in enumerate(columns, 1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


def _tx_dedup_key(tx: dict) -> str:
    return f"{tx.get('date')}|{tx.get('name', '').strip().lower()}|{abs(tx.get('amount', 0)):.2f}"


def _existing_keys(ws) -> set:
    keys = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] and row[1]:
            # Date | Description | Account | Category | Type | Amount
            date_val = str(row[0]) if row[0] else ""
            desc_val = str(row[1]).strip().lower() if row[1] else ""
            amt_val = f"{abs(float(row[5])):.2f}" if row[5] is not None else "0.00"
            keys.add(f"{date_val}|{desc_val}|{amt_val}")
    return keys


def _build_summary_sheet(wb, year: int):
    """Create Monthly Summary sheet with SUMIFS formulas and a stacked bar chart."""
    ws = wb.create_sheet("Monthly Summary")
    ws.column_dimensions["A"].width = 28

    # Row 1: title; Row 2: month headers
    ws["A1"] = f"Category"
    ws["A1"].font = HEADER_FONT
    ws["A1"].fill = HEADER_FILL

    for m_idx, month in enumerate(MONTHS, 2):
        cell = ws.cell(row=2, column=m_idx, value=month)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(m_idx)].width = 11

    total_col = len(MONTHS) + 2
    total_cell = ws.cell(row=2, column=total_col, value="Total")
    total_cell.font = HEADER_FONT
    total_cell.fill = HEADER_FILL
    total_cell.alignment = Alignment(horizontal="center")
    ws.column_dimensions[get_column_letter(total_col)].width = 13

    ws.freeze_panes = "B3"
    return ws


def _build_ytd_sheet(wb, year: int):
    ws = wb.create_sheet("YTD Summary")
    _header_row(ws, ["Category", "YTD Total", "% of Total"], row=1)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 12
    return ws


def _build_yoy_sheet(wb):
    ws = wb.create_sheet("YoY Trends")
    ws["A1"] = "Year-over-year comparison requires 12+ months of data to populate Last Year column."
    ws["A1"].font = Font(name="Arial", italic=True, color="888888")
    _header_row(ws, ["Category", "This Year YTD", "Last Year Same Period", "$ Change", "% Change"], row=2)
    ws.column_dimensions["A"].width = 28
    for col in "BCDE":
        ws.column_dimensions[col].width = 18
    return ws


def _refresh_summary_formulas(wb, year: int):
    """Rewrite Monthly Summary, YTD Summary, and YoY Trends formulas from Transactions data."""
    tx_ws = wb["Transactions"]

    # Collect all unique expense categories from data rows
    categories = []
    seen = set()
    for row in tx_ws.iter_rows(min_row=2, values_only=True):
        if row[3] and row[4] == "Expense" and row[3] not in seen:
            seen.add(row[3])
            categories.append(row[3])

    # ---- Monthly Summary ----
    ms = wb["Monthly Summary"]
    # Clear data rows (keep header rows 1-2)
    for row in ms.iter_rows(min_row=3):
        for cell in row:
            cell.value = None

    data_start_row = 3
    total_col = len(MONTHS) + 2  # col 14 (N)

    for r_idx, cat in enumerate(categories, data_start_row):
        ms.cell(row=r_idx, column=1, value=cat).font = Font(name="Arial", size=10)
        cat_fill = _category_fill(cat)
        ms.cell(row=r_idx, column=1).fill = cat_fill

        for m_idx, _ in enumerate(MONTHS, 2):
            month_num = m_idx - 1
            # Start/end of month as date literals Plaid stores as YYYY-MM-DD strings
            start = f"{year}-{month_num:02d}-01"
            end_day = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month_num - 1]
            if month_num == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
                end_day = 29
            end = f"{year}-{month_num:02d}-{end_day:02d}"

            formula = (
                f'=SUMPRODUCT((Transactions!D$2:D$10000="{cat}")*'
                f'(Transactions!E$2:E$10000="Expense")*'
                f'(Transactions!A$2:A$10000>="{start}")*'
                f'(Transactions!A$2:A$10000<="{end}")*'
                f'Transactions!F$2:F$10000)'
            )
            cell = ms.cell(row=r_idx, column=m_idx, value=formula)
            cell.number_format = CURRENCY_FMT
            cell.font = Font(name="Arial", size=10)
            cell.fill = cat_fill

        # Total column
        b_col = get_column_letter(2)
        m_col = get_column_letter(1 + len(MONTHS))
        ms.cell(row=r_idx, column=total_col,
                value=f"=SUM({b_col}{r_idx}:{m_col}{r_idx})").number_format = CURRENCY_FMT

    # Total row
    if categories:
        total_row = data_start_row + len(categories)
        ms.cell(row=total_row, column=1, value="TOTAL").font = Font(name="Arial", bold=True, size=10)
        for m_idx in range(2, len(MONTHS) + 3):
            col_ltr = get_column_letter(m_idx)
            ms.cell(row=total_row, column=m_idx,
                    value=f"=SUM({col_ltr}{data_start_row}:{col_ltr}{total_row - 1})").number_format = CURRENCY_FMT

        # Add stacked bar chart
        try:
            chart = BarChart()
            chart.type = "col"
            chart.grouping = "stacked"
            chart.title = f"Monthly Spending by Category — {year}"
            chart.y_axis.title = "Amount ($)"
            chart.x_axis.title = "Month"
            chart.width = 30
            chart.height = 18

            for r_idx2 in range(data_start_row, total_row):
                data_ref = Reference(ms, min_col=2, max_col=len(MONTHS) + 1,
                                     min_row=r_idx2, max_row=r_idx2)
                from openpyxl.chart import Series
                series = Series(data_ref, title=ms.cell(row=r_idx2, column=1).value)
                chart.series.append(series)

            cats_ref = Reference(ms, min_col=2, max_col=len(MONTHS) + 1, min_row=2)
            chart.set_categories(cats_ref)
            ms.add_chart(chart, f"A{total_row + 2}")
        except Exception:
            pass  # chart is a nice-to-have

    # ---- YTD Summary ----
    ytd = wb["YTD Summary"]
    for row in ytd.iter_rows(min_row=2):
        for cell in row:
            cell.value = None

    today = date.today().strftime("%Y-%m-%d")
    ytd_start = f"{year}-01-01"

    for r_idx, cat in enumerate(categories, 2):
        ytd.cell(row=r_idx, column=1, value=cat).font = Font(name="Arial", size=10)
        formula = (
            f'=SUMPRODUCT((Transactions!D$2:D$10000="{cat}")*'
            f'(Transactions!E$2:E$10000="Expense")*'
            f'(Transactions!A$2:A$10000>="{ytd_start}")*'
            f'(Transactions!A$2:A$10000<="{today}")*'
            f'Transactions!F$2:F$10000)'
        )
        ytd.cell(row=r_idx, column=2, value=formula).number_format = CURRENCY_FMT

    # % of total
    if categories:
        last_data = 1 + len(categories)
        total_cell_addr = f"B{last_data + 1}"
        ytd.cell(row=last_data + 1, column=1, value="TOTAL").font = Font(name="Arial", bold=True)
        ytd.cell(row=last_data + 1, column=2,
                 value=f"=SUM(B2:B{last_data})").number_format = CURRENCY_FMT

        for r_idx in range(2, last_data + 1):
            ytd.cell(row=r_idx, column=3,
                     value=f"=IF({total_cell_addr}=0,\"\",B{r_idx}/{total_cell_addr})").number_format = "0.0%"

    # ---- YoY Trends ----
    yoy = wb["YoY Trends"]
    for row in yoy.iter_rows(min_row=3):
        for cell in row:
            cell.value = None

    last_year = year - 1
    last_year_start = f"{last_year}-01-01"
    last_year_end = date(last_year, date.today().month, date.today().day).strftime("%Y-%m-%d")

    for r_idx, cat in enumerate(categories, 3):
        yoy.cell(row=r_idx, column=1, value=cat).font = Font(name="Arial", size=10)
        ytd_f = (
            f'=SUMPRODUCT((Transactions!D$2:D$10000="{cat}")*'
            f'(Transactions!E$2:E$10000="Expense")*'
            f'(Transactions!A$2:A$10000>="{ytd_start}")*'
            f'(Transactions!A$2:A$10000<="{today}")*'
            f'Transactions!F$2:F$10000)'
        )
        ly_f = (
            f'=SUMPRODUCT((Transactions!D$2:D$10000="{cat}")*'
            f'(Transactions!E$2:E$10000="Expense")*'
            f'(Transactions!A$2:A$10000>="{last_year_start}")*'
            f'(Transactions!A$2:A$10000<="{last_year_end}")*'
            f'Transactions!F$2:F$10000)'
        )
        yoy.cell(row=r_idx, column=2, value=ytd_f).number_format = CURRENCY_FMT
        yoy.cell(row=r_idx, column=3, value=ly_f).number_format = CURRENCY_FMT
        yoy.cell(row=r_idx, column=4, value=f"=B{r_idx}-C{r_idx}").number_format = CURRENCY_FMT
        yoy.cell(row=r_idx, column=5, value=f"=IF(C{r_idx}=0,\"\",B{r_idx}/C{r_idx}-1)").number_format = "0.0%"


def write_spending_ledger(filepath: str, new_transactions: list) -> dict:
    year = date.today().year

    if os.path.exists(filepath):
        wb = load_workbook(filepath)
        if "Transactions" not in wb.sheetnames:
            wb.create_sheet("Transactions", 0)
        if "Monthly Summary" not in wb.sheetnames:
            _build_summary_sheet(wb, year)
        if "YTD Summary" not in wb.sheetnames:
            _build_ytd_sheet(wb, year)
        if "YoY Trends" not in wb.sheetnames:
            _build_yoy_sheet(wb)
    else:
        wb = Workbook()
        wb.remove(wb.active)
        tx_ws = wb.create_sheet("Transactions")
        _header_row(tx_ws, ["Date", "Description", "Account", "Category", "Type", "Amount"])
        tx_ws.column_dimensions["A"].width = 12
        tx_ws.column_dimensions["B"].width = 38
        tx_ws.column_dimensions["C"].width = 18
        tx_ws.column_dimensions["D"].width = 30
        tx_ws.column_dimensions["E"].width = 10
        tx_ws.column_dimensions["F"].width = 14
        tx_ws.freeze_panes = "A2"
        _build_summary_sheet(wb, year)
        _build_ytd_sheet(wb, year)
        _build_yoy_sheet(wb)

    tx_ws = wb["Transactions"]
    existing_keys = _existing_keys(tx_ws)

    added = 0
    skipped = 0
    for tx in sorted(new_transactions, key=lambda t: t.get("date", ""), reverse=True):
        key = _tx_dedup_key(tx)
        if key in existing_keys:
            skipped += 1
            continue

        cat = tx.get("category", "Uncategorized")
        row_data = [
            tx.get("date"),
            tx.get("name", ""),
            tx.get("account_label", ""),
            cat,
            tx.get("type", "Expense"),
            tx.get("amount", 0.0),
        ]
        row_idx = tx_ws.max_row + 1
        row_fill = _category_fill(cat)
        for col_idx, val in enumerate(row_data, 1):
            cell = tx_ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = BODY_FONT
            cell.fill = row_fill
            if col_idx == 6:
                cell.number_format = CURRENCY_FMT

        existing_keys.add(key)
        added += 1

    if added > 0:
        _refresh_summary_formulas(wb, year)

    wb.save(filepath)
    return {"added": added, "skipped": skipped}
