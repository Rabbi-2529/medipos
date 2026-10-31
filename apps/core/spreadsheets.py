"""Reading uploaded .xlsx/.csv files and writing .xlsx downloads."""
import csv
import io
import re

from django.http import HttpResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SpreadsheetError(Exception):
    pass


def normalize_header(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def read_table(upload):
    """(headers, rows) from the first sheet of an .xlsx or a .csv upload. Blank rows are skipped."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise SpreadsheetError("The file is larger than 10 MB.")
    name = upload.name.lower()
    if name.endswith(".xlsx"):
        try:
            sheet = load_workbook(upload, read_only=True, data_only=True).worksheets[0]
        except Exception as error:  # openpyxl raises many kinds for broken files
            raise SpreadsheetError(f"Couldn't read the Excel file ({error.__class__.__name__}).")
        rows = sheet.iter_rows(values_only=True)
    elif name.endswith(".csv"):
        raw = upload.read()
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        rows = csv.reader(io.StringIO(text))
    else:
        raise SpreadsheetError("Upload an .xlsx or .csv file.")

    rows = iter(rows)
    headers = next(rows, None)
    if not headers:
        raise SpreadsheetError("The file is empty.")
    data = []
    for values in rows:
        values = ["" if v is None else v for v in values]
        if any(str(v).strip() for v in values):
            data.append(values)
    return [str(h or "").strip() for h in headers], data


def map_headers(headers, aliases):
    """{field: column index} using `aliases` = {field: [accepted header spellings]}."""
    lookup = {}
    for field, names in aliases.items():
        for name in names:
            lookup[normalize_header(name)] = field
    mapping = {}
    for index, header in enumerate(headers):
        field = lookup.get(normalize_header(header))
        if field and field not in mapping:
            mapping[field] = index
    return mapping


def xlsx_response(filename, headers, rows, *, notes=None):
    """Stream a simple one-sheet workbook as a download."""
    workbook = Workbook(write_only=False)
    sheet = workbook.active
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="6D5DFC")
    for row in rows:
        sheet.append(list(row))
    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = max(12, len(header) + 4)
    sheet.freeze_panes = "A2"
    if notes:
        info = workbook.create_sheet("How to fill")
        for line in notes:
            info.append([line])
        info.column_dimensions["A"].width = 110
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
