"""Treat all user-controlled CSV/XLSX strings as text, never formulas.

Used only at export boundaries: database values and attendance calculations are
unchanged. Numeric Python values stay numeric; negative numeric strings are text.
"""


def spreadsheet_cell(value):
    if not isinstance(value, str) or not value:
        return value
    # Spreadsheet importers can trim control/space characters before interpreting
    # a formula. Preserve the original value and prefix it with a text marker.
    stripped = value.lstrip()
    if value[0] in ('\t', '\r', '\n') or (stripped and stripped[0] in '=+-@'):
        return "'" + value
    return value


def spreadsheet_row(values):
    return [spreadsheet_cell(value) for value in values]
