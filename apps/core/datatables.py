"""Server-side processing for DataTables (https://datatables.net/manual/server-side).

Usage in a view:

    return datatable_json(request, queryset, order_columns=[...], row=render_row, search=apply_search)

`order_columns` lines up with the table's columns; None marks a column as not sortable.
"""
from django.http import JsonResponse


def _int(value, default, low=0, high=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    number = max(low, number)
    return min(high, number) if high is not None else number


def datatable_json(request, queryset, *, order_columns, row, search=None, max_length=100):
    params = request.GET
    draw = _int(params.get("draw"), 0)
    start = _int(params.get("start"), 0)
    length = _int(params.get("length"), 25, low=1, high=max_length)

    total = queryset.count()
    term = params.get("search[value]", "").strip()
    if term and search is not None:
        queryset = search(queryset, term)
        filtered = queryset.count()
    else:
        filtered = total

    column = _int(params.get("order[0][column]"), -1, low=-1)
    if 0 <= column < len(order_columns) and order_columns[column]:
        prefix = "-" if params.get("order[0][dir]") == "desc" else ""
        queryset = queryset.order_by(f"{prefix}{order_columns[column]}", "pk")

    return JsonResponse({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": [row(obj) for obj in queryset[start:start + length]],
    })
