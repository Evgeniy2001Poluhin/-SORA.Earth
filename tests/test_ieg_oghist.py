"""scripts/ieg_oghist.py against synthetic workbooks and a fake transport. Nothing here reaches the network.

The script is loaded from its file, as tests/test_ieg_approval_fy.py does. The
workbooks are built here with zipfile, so each test states its own layout. Every
network path is replaced by a fake transport, or by a guard that fails the test
if it is reached. This file imports no network module; test_no_network_import_in_this_file
checks that by reading the source.
"""
import ast
import csv
import hashlib
import importlib.util
import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "ieg_oghist.py"
_spec = importlib.util.spec_from_file_location("ieg_oghist", _SCRIPT)
oh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oh)

SHEET = "Country Analytical History"
FY_LABEL = "Bank's fiscal year:"
YEAR_LABEL = "Data for calendar year :"
CT_WORKBOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
CT_MACRO = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

URL = "https://ddh-openapi.worldbank.org/resources/DR0095334/download"
PAGE = "https://datahelpdesk.worldbank.org/knowledgebase/articles/906519-world-bank-country-and-lending-groups"
NAME = "OGHIST_2026_10_08.xlsx"
GOOD_HEADERS = {
    "content-type": "application/octet-stream",
    "content-disposition": f"attachment; filename={NAME}; filename*=UTF-8''{NAME}",
}
NOTE_TEXT = (
    "Note: Income classifications are set each year on July 1 for all World Bank member economies. "
    "These official analytical classifications are fixed during the World Bank's fiscal year (ending on June 30), "
    "thus economies remain in the categories in which they are classified. "
    "The historical classifications shown are as published on July 1 of each fiscal year."
)
PARAMS_DEFAULT = (
    ("Label", "Value"),
    ("Fiscal Yeay", "FY21"),
    ("Publication Date", "(July 2020)"),
    ("Low Income Threshold", "$1,035 or less"),
    ("Lower Middle Income Range", "$1,036  to $4,045"),
    ("Upper Middle Income Range", "$4,046 to $12,535"),
    ("High Income Threshold", "$12,536 or more"),
    ("Number of economies", "189"),
    ("GNI Year", "2019"),
    ("Term of Income Classification", "until 1 July 2021"),
)
PAGE_FILE = "source-page/906519-world-bank-country-and-lending-groups.html"
GOOD_PAGE_HTML = (
    b'<html><body><a href="' + URL.encode("utf-8")
    + b'">historical classification by income in XLSX format</a></body></html>'
)
GOOD_PAGE_HEADERS = {"content-type": "text/html; charset=utf-8"}
RECORD = {
    "page_url": PAGE, "resource_url": URL, "final_url": URL, "name": NAME, "bytes": 0, "sha256": "0" * 64,
    "content_type": "application/octet-stream", "content_disposition": None, "fetched_at_utc": "2026-10-16T00:00:00Z",
    "page": {"file": PAGE_FILE, "bytes": len(GOOD_PAGE_HTML), "sha256": hashlib.sha256(GOOD_PAGE_HTML).hexdigest(),
             "http_status": 200, "fetched_at_utc": "2026-10-16T00:00:00Z"},
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test that reaches a network path fails here, not at the socket."""

    def refuse(*args, **kwargs):
        raise AssertionError("a unit test reached a network path")

    monkeypatch.setattr(oh.urllib.request, "build_opener", refuse)
    monkeypatch.setattr(oh.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(oh.http.client.HTTPConnection, "connect", refuse)
    monkeypatch.setattr(oh.ssl, "create_default_context", refuse)


# --- synthetic workbooks ---------------------------------------------------------------


def _col(n):
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def xlsx(sheets, *, content_type=CT_WORKBOOK, extra_parts=None, hidden=()):
    """Build an .xlsx package. Strings go to the shared-string table, as Excel writes them.

    A cell spec is None (no cell), "" (an empty cell), a string, an int, or
    ("f", formula, cached) for a formula cell.
    """
    sst, index = [], {}

    def sidx(text):
        if text not in index:
            index[text] = len(sst)
            sst.append(text)
        return index[text]

    sheet_xml = []
    for _name, rows in sheets:
        body = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, spec in enumerate(row, start=1):
                if spec is None:
                    continue
                ref = f"{_col(c)}{r}"
                if isinstance(spec, tuple):
                    _tag, formula, cached = spec
                    cells.append(f'<c r="{ref}" t="str"><f>{escape(formula)}</f><v>{escape(cached)}</v></c>')
                elif spec == "":
                    cells.append(f'<c r="{ref}"/>')
                elif isinstance(spec, int):
                    cells.append(f'<c r="{ref}"><v>{spec}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="s"><v>{sidx(spec)}</v></c>')
            body.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml.append(f'<worksheet xmlns="{MAIN}"><sheetData>{"".join(body)}</sheetData></worksheet>')

    workbook = (
        f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
        + "".join(
            f'<sheet name="{escape(name)}" sheetId="{i}"'
            + (' state="hidden"' if name in hidden else "")
            + f' r:id="rId{i}"/>'
            for i, (name, _r) in enumerate(sheets, 1)
        )
        + "</sheets></workbook>"
    )
    rels = (
        f'<Relationships xmlns="{PKG_REL}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        + f'<Relationship Id="rIdSst" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = (
        f'<Types xmlns="{CONTENT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{content_type}"/>'
        f"{overrides}"
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    sst_xml = (
        f'<sst xmlns="{MAIN}" count="{len(sst)}" uniqueCount="{len(sst)}">'
        + "".join(f"<si><t>{escape(text)}</t></si>" for text in sst)
        + "</sst>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", sst_xml)
        for i, xml in enumerate(sheet_xml, 1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", xml)
        for name, data in (extra_parts or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def income_rows(countries, fys=(1989, 1990, 1991), lag=2, *, row5=FY_LABEL, row6=YEAR_LABEL,
                calendar=None, notes=True, footnotes=()):
    years = list(calendar) if calendar is not None else [fy - lag for fy in fys]
    rows = [
        [None, "World Bank Analytical Classifications"],
        [None, "(presented in World Development Indicators)"],
        [None, "GNI per capita in US$ (Atlas methodology)"],
        [],
        [None, row5, *[f"FY{fy % 100:02d}" for fy in fys]],
        [None, row6, *years],
    ]
    for code, name, values in countries:
        rows.append([code, name, *values])
    for text in footnotes:
        rows.append([None, text])
    if notes:
        rows.append([None, NOTE_TEXT])
    return rows


def oghist(countries, *, fys=(1989, 1990, 1991), lag=2, params=None, notes=True, footnotes=(),
           row5=FY_LABEL, row6=YEAR_LABEL, calendar=None, extra_sheets=(), sheet=SHEET, sheet_rows=None,
           hidden=()):
    """A workbook shaped like OGHIST: Parameters, Thresholds, the income sheet, and any extra sheets."""
    sheets = [
        ("Parameters", [list(r) for r in (PARAMS_DEFAULT if params is None else params)]),
        ("Thresholds", [["World Bank GNI per capita Operational"]]),
    ]
    rows = sheet_rows if sheet_rows is not None else income_rows(
        countries, fys, lag, row5=row5, row6=row6, calendar=calendar, notes=notes, footnotes=footnotes)
    sheets.append((sheet, rows))
    sheets += list(extra_sheets)
    return xlsx(sheets, hidden=hidden)


def derive(body, record=None):
    return oh.derive_income(oh.Workbook(body), RECORD if record is None else record)


def rows_of(body, **kwargs):
    rows, _qa, _notes, _parsing = derive(body, **kwargs)
    return rows


def row_for(rows, code, fy):
    found = [r for r in rows if r[0] == code and r[4] == str(fy)]
    assert len(found) == 1, f"expected one row for {code} FY{fy}, found {len(found)}"
    return found[0]


STANDARD = [
    ("AFG", "Afghanistan", ["L", "L", "L"]),
    ("ALB", "Albania", ["..", "", "LM"]),
    ("ARG", "Argentina", ["UM", "UM", "H"]),
]


class FakeTransport:
    """Answers the source page with `page` (a page that links the workbook by default), and the
    workbook URL with the given responses in order. Records every URL it was asked for."""

    def __init__(self, *responses, page=None):
        self.responses = list(responses)
        self.page = page if page is not None else resp(200, GOOD_PAGE_HTML, GOOD_PAGE_HEADERS, final_url=PAGE)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url == PAGE:
            return self.page
        if not self.responses:
            raise AssertionError("the fake transport has no workbook response left")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def resp(status=200, body=b"", headers=None, final_url=URL, redirects=()):
    return oh.Response(status=status, headers=dict(headers or {}), body=body, final_url=final_url,
                       redirects=list(redirects))


def build_net(tmp_path, body, *, responses=None, out_name="out", headers=None, **kwargs):
    transport = FakeTransport(*(responses or [resp(200, body, GOOD_HEADERS if headers is None else headers)]))
    out = tmp_path / out_name
    sha = oh.build(out, url=URL, page_url=PAGE, transport=transport, sleep=lambda s: None, **kwargs)
    return out, sha, transport


# --- the FY convention, from the file's own Parameters and notes ----------------------


def test_fiscal_year_of_puts_30_june_and_1_july_in_different_years():
    assert oh.fiscal_year_of(date(2021, 6, 30)) == 2021
    assert oh.fiscal_year_of(date(2021, 7, 1)) == 2022


def test_a_fiscal_year_is_the_half_open_interval_from_1_july_to_1_july():
    assert oh.fy_interval(2021) == (date(2020, 7, 1), date(2021, 7, 1))


def test_each_row_interval_agrees_with_the_fiscal_year_of_its_dates(tmp_path):
    rows = rows_of(oghist(STANDARD))
    for row in rows:
        fy = int(row[4])
        start, end = date.fromisoformat(row[5]), date.fromisoformat(row[6])
        assert oh.fiscal_year_of(start) == fy
        assert oh.fiscal_year_of(end) == fy + 1
        assert oh.fiscal_year_of(end - timedelta(days=1)) == fy


def test_a_hidden_parameters_sheet_is_read_and_the_notes_say_it_is_hidden():
    # In the real file the Parameters sheet is hidden; the convention must still come from it.
    body = oghist(STANDARD, hidden=("Parameters",))
    assert oh.Workbook(body).hidden_sheets() == ["Parameters"]
    _rows, _qa, notes, parsing = derive(body)
    assert parsing["hidden_sheets"] == ["Parameters"]
    assert "`Parameters` (hidden in the workbook): read: the year convention" in notes


def test_the_calendar_offset_is_read_from_the_parameters_sheet_not_assumed():
    # The file says GNI Year 2018 for FY21, so the calendar row is the fiscal year minus 3.
    params = [list(r) for r in PARAMS_DEFAULT]
    params[8] = ["GNI Year", "2018"]
    rows, qa, _notes, parsing = derive(oghist(STANDARD, lag=3, params=params))
    assert parsing["calendar_year_offset"] == 3
    assert row_for(rows, "AFG", 1989)[4] == "1989"
    assert qa["coverage"]["first_fiscal_year"] == 1989


def test_a_header_that_implies_a_different_offset_than_the_parameters_is_refused():
    # Parameters say the offset is 2, but the calendar row is the fiscal year minus 1: the +1 trap.
    body = oghist(STANDARD, calendar=[1988, 1989, 1990])
    with pytest.raises(oh.LayoutRefused, match="does not agree with calendar year"):
        derive(body)


def test_a_parameters_publication_date_that_is_not_1_july_of_the_year_before_is_refused():
    params = [list(r) for r in PARAMS_DEFAULT]
    params[2] = ["Publication Date", "(June 2020)"]
    with pytest.raises(oh.LayoutRefused, match="publication date is not 1 July"):
        derive(oghist(STANDARD, params=params))


def test_a_parameters_term_year_that_disagrees_with_the_label_is_refused():
    params = [list(r) for r in PARAMS_DEFAULT]
    params[9] = ["Term of Income Classification", "until 1 July 2022"]
    with pytest.raises(oh.LayoutRefused, match="disagrees with its term end date"):
        derive(oghist(STANDARD, params=params))


def test_a_missing_parameter_is_refused():
    params = [list(r) for r in PARAMS_DEFAULT if r[0] != "GNI Year"]
    with pytest.raises(oh.LayoutRefused, match="lacks"):
        derive(oghist(STANDARD, params=params))


def test_the_publication_note_must_be_in_the_file():
    with pytest.raises(oh.LayoutRefused, match="no note that the classifications are set"):
        derive(oghist(STANDARD, notes=False))


def test_a_header_with_a_gap_in_its_fiscal_years_is_refused():
    body = oghist(STANDARD, fys=(1989, 1991), calendar=[1987, 1989])
    with pytest.raises(oh.LayoutRefused, match="not consecutive"):
        derive(body)


def test_a_changed_row_5_label_is_a_controlled_refusal():
    with pytest.raises(oh.LayoutRefused, match="B5 is"):
        derive(oghist(STANDARD, row5="Fiscal year:"))


def test_a_changed_row_6_label_is_a_controlled_refusal():
    with pytest.raises(oh.LayoutRefused, match="B6 is"):
        derive(oghist(STANDARD, row6="Calendar year:"))


def test_a_missing_income_sheet_is_a_controlled_refusal():
    with pytest.raises(oh.LayoutRefused, match="has no sheet"):
        derive(oghist(STANDARD, sheet="Renamed"))


def test_a_header_with_an_empty_column_inside_the_block_is_refused():
    rows = income_rows(STANDARD)
    rows[4][3] = None  # FY90 disappears from the middle of the block, in both header rows
    rows[5][3] = None
    with pytest.raises(oh.LayoutRefused, match="unbroken block"):
        derive(oghist(STANDARD, sheet_rows=rows))


def test_a_formula_in_a_header_cell_is_refused():
    rows = income_rows(STANDARD)
    rows[4][2] = ("f", "CONCATENATE(\"FY\",\"89\")", "FY89")
    with pytest.raises(oh.SourceRefused, match="formula"):
        derive(oghist(STANDARD, sheet_rows=rows))


# --- the mapping and the refusals ------------------------------------------------------


def test_codes_map_to_the_world_bank_groups():
    rows = rows_of(oghist([
        ("AAA", "Alpha", ["L", "LM", "UM"]),
        ("BBB", "Bravo", ["H", "H", "H"]),
    ]))
    assert row_for(rows, "AAA", 1989)[7] == "LIC"
    assert row_for(rows, "AAA", 1990)[7] == "LMC"
    assert row_for(rows, "AAA", 1991)[7] == "UMC"
    assert row_for(rows, "BBB", 1989)[7] == "HIC"


def test_each_row_has_the_locator_of_its_cell_and_its_rule():
    rows = rows_of(oghist(STANDARD))
    row = row_for(rows, "ARG", 1991)
    assert row[8] == f"{SHEET}!E{7 + 2}"  # ARG is the third country row, data starts at row 7
    assert row[9] == oh.RULE_GROUP


def test_a_missing_early_fy_gives_no_row_and_counts_as_unknown():
    rows, qa, _notes, _parsing = derive(oghist(STANDARD))
    assert [r for r in rows if r[0] == "ALB"] == [row_for(rows, "ALB", 1991)]
    assert qa["counts"]["unknown_dotdot"] == 1
    assert qa["counts"]["unknown_blank"] == 1


def test_an_unknown_non_blank_group_code_is_refused():
    with pytest.raises(oh.SourceRefused, match="not a documented income code"):
        derive(oghist([("AAA", "Alpha", ["X", "L", "L"])]))


def test_a_lower_case_group_code_is_refused_not_guessed():
    with pytest.raises(oh.SourceRefused, match="not a documented income code"):
        derive(oghist([("AAA", "Alpha", ["h", "L", "L"])]))


def test_a_formula_in_an_income_cell_is_refused_even_with_a_valid_cached_value():
    rows = income_rows([("AAA", "Alpha", ["L", "L", "L"])])
    rows[6][2] = ("f", "IF(TRUE,\"H\",\"L\")", "H")
    with pytest.raises(oh.SourceRefused, match="formula"):
        derive(oghist([], sheet_rows=rows))


def test_a_formula_in_a_country_code_is_refused():
    rows = income_rows([("AAA", "Alpha", ["L", "L", "L"])])
    rows[6][0] = ("f", "\"AAA\"", "AAA")
    with pytest.raises(oh.SourceRefused, match="formula"):
        derive(oghist([], sheet_rows=rows))


def test_lm_with_a_star_but_no_footnote_is_refused():
    with pytest.raises(oh.SourceRefused, match="no footnote"):
        derive(oghist([("YEM", "Yemen, Rep.", ["LM*", "LM", "LM"])]))


def test_the_yemen_footnote_value_is_kept_as_lmc_and_routed_to_review():
    footnote = "* At this time, there were Yemen, PDR (L) and Yemen, Arab Rep. (LM); combined they would have been LM."
    rows, qa, _notes, _parsing = derive(oghist(
        [("YEM", "Yemen, Rep.", ["LM*", "LM*", "LM"])], footnotes=[footnote]))
    row = row_for(rows, "YEM", 1989)
    assert row[7] == "LMC" and row[9] == oh.RULE_STAR
    assert qa["review_for_B"]["footnoted_values"] == [f"{SHEET}!C7", f"{SHEET}!D7"]
    assert qa["counts"]["footnoted_values"] == 2


def test_a_former_entity_keeps_its_code_and_is_routed_to_review():
    rows, qa, _notes, _parsing = derive(oghist([("YUG", "Serbia and Montenegro (former)", ["LM", "LM", "LM"])]))
    assert row_for(rows, "YUG", 1989)[9] == oh.RULE_FORMER
    assert [e["code"] for e in qa["review_for_B"]["former_entities"]] == ["YUG"]


def test_a_code_with_a_lower_case_suffix_is_kept_as_the_file_has_it_and_listed():
    rows, qa, _notes, _parsing = derive(oghist([("YUGf", "Yugoslavia (former)", ["UM", "UM", "UM"])]))
    assert row_for(rows, "YUGf", 1989)[0] == "YUGf"
    assert qa["counts"]["codes_outside_three_letters"] == ["YUGf"]


def test_a_country_code_that_is_not_a_world_bank_shape_is_refused():
    with pytest.raises(oh.SourceRefused, match="not a World Bank country code"):
        derive(oghist([("ZZZZZ", "Nowhere", ["L", "L", "L"])]))


def test_a_country_row_without_a_name_is_refused():
    with pytest.raises(oh.SourceRefused, match="no country name"):
        derive(oghist([("AAA", "", ["L", "L", "L"])]))


def test_a_value_outside_the_fiscal_year_block_is_a_controlled_refusal():
    rows = income_rows([("AAA", "Alpha", ["L", "L", "L"])])
    rows[6] = rows[6] + [None, None, None, "stray"]
    with pytest.raises(oh.LayoutRefused, match="beyond the fiscal-year block"):
        derive(oghist([], sheet_rows=rows))


def test_conflicting_repeats_of_a_code_are_refused():
    with pytest.raises(oh.SourceRefused, match="different values"):
        derive(oghist([("AAA", "Alpha", ["L", "L", "L"]), ("AAA", "Alpha", ["H", "L", "L"])]))


def test_identical_repeats_of_a_code_are_dropped_and_counted():
    rows, qa, _notes, _parsing = derive(oghist([("AAA", "Alpha", ["L", "L", "L"]), ("AAA", "Alpha", ["L", "L", "L"])]))
    assert len(rows) == 3
    assert qa["counts"]["identical_repeats_dropped"] == 1


def test_unsorted_rows_give_identical_normalized_output_and_locators_that_still_resolve():
    # raw_cell_locator is the physical cell, so it moves when the rows move. Everything else is the same,
    # and every locator still points at a cell with the same country and value in the reordered file.
    rows_a, qa_a, _notes_a, _ = derive(oghist(STANDARD))
    body_b = oghist(list(reversed(STANDARD)))
    rows_b, qa_b, _notes_b, _ = derive(body_b)
    without_locator = lambda rows: [r[:8] + r[9:] for r in rows]  # noqa: E731
    assert without_locator(rows_a) == without_locator(rows_b)
    assert qa_a == qa_b
    assert [r[0] for r in rows_b] == sorted(r[0] for r in rows_b), "sorted by code, then fiscal year"

    sheet = oh.Workbook(body_b).sheet(SHEET)
    for row in rows_b:
        ref = row[8].split("!", 1)[1]
        column_letters, row_number = oh._CELL_REF.fullmatch(ref).groups()
        cell = sheet.cell(int(row_number), oh._column_index(column_letters))
        assert cell is not None and cell.text in ("H", "L", "LM", "UM", "LM*"), ref
        assert sheet.cell(int(row_number), 1).text == row[0], ref
        header_column = oh._column_index(column_letters)
        assert sheet.cell(5, header_column).text == f"FY{int(row[4]) % 100:02d}", ref


def test_the_income_csv_has_exactly_the_documented_columns():
    rows = rows_of(oghist(STANDARD))
    text = oh._csv_bytes(oh.INCOME_COLUMNS, rows).decode("utf-8")
    header = next(csv.reader(io.StringIO(text)))
    assert header == [
        "wb_code", "code_system", "iso3", "country_name", "effective_fy", "effective_start",
        "effective_end", "income_group", "raw_cell_locator", "rule_id",
    ]
    assert all(r[1] == "WB" and r[2] == "" for r in rows)


def test_the_notes_quote_the_file_and_do_not_paraphrase_it():
    _rows, _qa, notes, _parsing = derive(oghist(STANDARD))
    assert f"`{NOTE_TEXT}`" in notes, "the publication note is quoted in full"
    assert "| Fiscal Yeay | FY21 |" in notes
    assert "| GNI Year | 2019 |" in notes
    assert "| C | FY89 | 1987 | 1989 |" in notes, "the header table quotes row 5 and row 6 per column"
    assert "Licence: not stated on the source page" in notes and "Not verified" in notes
    assert f"`{PAGE_FILE}`" in notes, "the source page evidence is named in the notes"


# --- the body: what is refused before any parser reads it -----------------------------


def test_an_html_document_served_as_a_workbook_is_refused():
    with pytest.raises(oh.SourceRefused, match="HTML"):
        oh.Workbook(b"<!DOCTYPE html><html><body>Access denied</body></html>")


def test_an_html_fragment_without_a_doctype_is_refused():
    with pytest.raises(oh.SourceRefused, match="HTML"):
        oh.Workbook(b"  \r\n<html><head></head></html>")


def test_an_empty_body_is_refused():
    with pytest.raises(oh.SourceRefused, match="empty"):
        oh.Workbook(b"")


def test_a_legacy_xls_is_refused():
    with pytest.raises(oh.SourceRefused, match="legacy"):
        oh.Workbook(oh.OLE_MAGIC + b"\x00" * 200)


def test_a_truncated_zip_is_refused():
    body = oghist(STANDARD)
    with pytest.raises(oh.SourceRefused, match="truncated or corrupt"):
        oh.Workbook(body[:-60])


def test_a_stored_part_with_a_flipped_byte_fails_its_crc():
    body = oghist(STANDARD)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(body)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as dst:
        for info in src.infolist():
            dst.writestr(info.filename, src.read(info.filename))
    damaged = bytearray(buf.getvalue())
    at = damaged.index(b"<sheetData>") + 1  # inside the stored sheet part: 's' becomes 'S'
    damaged[at] ^= 0x20
    with pytest.raises(oh.SourceRefused, match="CRC"):
        oh.Workbook(bytes(damaged))


def test_a_macro_enabled_workbook_is_refused():
    with pytest.raises(oh.SourceRefused, match="macro-enabled"):
        oh.Workbook(xlsx([("Parameters", [["Label"]])], content_type=CT_MACRO))


def test_a_workbook_with_a_vba_project_is_refused():
    body = xlsx([("Parameters", [["Label"]])], extra_parts={"xl/vbaProject.bin": b"\x00"})
    with pytest.raises(oh.SourceRefused, match="VBA"):
        oh.Workbook(body)


def test_a_dtd_in_a_part_is_refused():
    body = xlsx([("Parameters", [["Label"]])])
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(body)) as src, zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/sharedStrings.xml":
                data = data.replace(b"<sst", b"<!DOCTYPE sst [<!ENTITY x 'y'>]><sst", 1)
            dst.writestr(info.filename, data)
    with pytest.raises(oh.SourceRefused, match="DTD"):
        oh.Workbook(buf.getvalue())


def test_a_sheet_that_is_not_read_is_still_listed_in_the_notes(tmp_path):
    body = oghist(STANDARD, extra_sheets=[("Operational Category Change", [["Country", "Fiscal year"]])])
    _rows, _qa, notes, parsing = derive(body)
    assert "Operational Category Change" in parsing["sheets_not_read"]
    assert "`Operational Category Change`: not read" in notes


# --- source-name rules, redirects and retries ---------------------------------------


def test_the_workbook_name_comes_from_content_disposition_first():
    r = resp(200, b"x", {"content-disposition": "attachment; filename=OGHIST_2026_10_08.xlsx"})
    assert oh.raw_name(r, URL) == ("OGHIST_2026_10_08.xlsx", "content-disposition")


def test_an_rfc5987_filename_is_decoded():
    r = resp(200, b"x", {"content-disposition": "attachment; filename*=UTF-8''OGHIST%5F2026.xlsx"})
    assert oh.raw_name(r, URL) == ("OGHIST_2026.xlsx", "content-disposition")


def test_a_name_with_a_path_separator_is_not_used():
    r = resp(200, b"x", {"content-disposition": 'attachment; filename="../../etc/passwd.xlsx"'}, final_url=URL)
    name, source = oh.raw_name(r, URL)
    assert "/" not in name and source != "content-disposition"


def test_without_a_name_the_fallback_is_the_resource_id_and_is_labelled_as_one():
    r = resp(200, b"x", {}, final_url=URL)
    assert oh.raw_name(r, URL) == ("DR0095334.bin", "fallback-resource-id")


def test_the_url_path_name_is_used_when_the_header_has_none():
    r = resp(200, b"x", {}, final_url="https://files.example.org/x/OGHIST.xlsx")
    assert oh.raw_name(r, URL) == ("OGHIST.xlsx", "url-path")


def test_a_redirect_is_recorded_as_a_hop():
    recorder = oh._RedirectRecorder()
    request = oh.urllib.request.Request(URL)
    recorder.redirect_request(request, None, 302, "Found", {}, "https://files.example.org/OGHIST.xlsx")
    assert recorder.hops == [{"status": 302, "from": URL, "to": "https://files.example.org/OGHIST.xlsx"}]


def test_a_redirect_to_plain_http_is_refused():
    t = FakeTransport(resp(200, b"x", {}, redirects=[{"status": 302, "from": URL, "to": "http://files.example.org/x"}]))
    with pytest.raises(oh.FetchError, match="not an https URL"):
        oh.fetch(URL, transport=t, sleep=lambda s: None)


def test_a_non_https_resource_is_refused_before_any_request():
    t = FakeTransport()
    with pytest.raises(oh.FetchError, match="not an https URL"):
        oh.fetch("http://example.org/x", transport=t, sleep=lambda s: None)
    assert t.calls == []


def test_a_404_is_not_retried():
    t = FakeTransport(resp(404))
    sleeps = []
    with pytest.raises(oh.FetchError, match="HTTP 404 is not retried"):
        oh.fetch(URL, transport=t, sleep=sleeps.append)
    assert len(t.calls) == 1 and sleeps == []


def test_a_429_is_retried_and_the_third_attempt_can_succeed():
    t = FakeTransport(resp(429), resp(429), resp(200, b"ok"))
    sleeps = []
    r, attempts = oh.fetch(URL, transport=t, sleep=sleeps.append)
    assert r.body == b"ok"
    assert len(attempts) == 3 and sleeps == [oh.RETRY_WAIT_S[0], oh.RETRY_WAIT_S[1]]


def test_a_5xx_exhausts_the_three_attempts():
    t = FakeTransport(resp(503), resp(500), resp(502))
    sleeps = []
    with pytest.raises(oh.FetchError, match="3 attempts failed"):
        oh.fetch(URL, transport=t, sleep=sleeps.append)
    assert len(t.calls) == 3 and len(sleeps) == 2


def test_a_timeout_is_retried_and_then_refused():
    t = FakeTransport(TimeoutError("timed out"), TimeoutError("timed out"), TimeoutError("timed out"))
    with pytest.raises(oh.FetchError, match="transport_error"):
        oh.fetch(URL, transport=t, sleep=lambda s: None)
    assert len(t.calls) == 3


def test_a_timeout_followed_by_a_success_is_a_success():
    t = FakeTransport(TimeoutError("timed out"), resp(200, b"ok"))
    r, attempts = oh.fetch(URL, transport=t, sleep=lambda s: None)
    assert r.body == b"ok" and attempts[0]["outcome"] == "transport_error"


def test_a_body_over_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(oh, "MAX_BYTES", 100)

    class Stream:
        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def read(self, n):
            return self._buf.read(n)

    with pytest.raises(oh.OversizeError, match="exceeds"):
        oh._read_capped(Stream(b"x" * 101))
    assert oh._read_capped(Stream(b"x" * 100)) == b"x" * 100


def test_an_oversize_response_is_not_retried():
    t = FakeTransport(oh.OversizeError("too big"))
    with pytest.raises(oh.OversizeError):
        oh.fetch(URL, transport=t, sleep=lambda s: None)
    assert len(t.calls) == 1


# --- raw bytes, the fetch record, and the output protocol -----------------------------


def test_the_raw_bytes_are_saved_exactly_as_received(tmp_path):
    body = oghist(STANDARD)
    out, sha, _t = build_net(tmp_path, body)
    assert (out / "raw" / NAME).read_bytes() == body
    fetch = json.loads((out / "fetch.json").read_text(encoding="utf-8"))
    assert fetch["sha256"] == hashlib.sha256(body).hexdigest() and fetch["bytes"] == len(body)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["raw"] == {"file": f"raw/{NAME}", "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def test_the_manifest_sha_is_printed_as_the_sha_of_the_manifest_file(tmp_path, capsys):
    body = oghist(STANDARD)
    out, sha, _t = build_net(tmp_path, body)
    assert sha == hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()


def test_every_derived_hash_in_the_manifest_matches_its_file(tmp_path):
    out, _sha, _t = build_net(tmp_path, oghist(STANDARD))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["outputs"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == meta["sha256"], name
    assert sorted(manifest["outputs"]) == sorted([
        "fetch.json", "income_history.csv", "income_schema_notes.md", "qa.json", "raw/" + NAME, PAGE_FILE,
    ])


def test_a_html_200_is_refused_but_its_bytes_stay_as_evidence(tmp_path):
    html = b"<!DOCTYPE html><html><body>Please log in</body></html>"
    out = tmp_path / "out"
    with pytest.raises(oh.SourceRefused, match="HTML"):
        build_net(tmp_path, html, headers={"content-type": "text/html"})
    assert not out.exists()
    partial = tmp_path / "out.partial"
    assert (partial / "raw" / "DR0095334.bin").read_bytes() == html
    assert (partial / PAGE_FILE).read_bytes() == GOOD_PAGE_HTML, "the page is kept too"
    assert (partial / "fetch.json").is_file()


def test_a_refused_build_names_no_final_directory(tmp_path):
    with pytest.raises(oh.SourceRefused):
        build_net(tmp_path, b"not a workbook at all")
    assert not (tmp_path / "out").exists()


def test_a_non_empty_destination_is_refused_and_left_alone(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("someone's file", encoding="utf-8")
    transport = FakeTransport(resp(200, oghist(STANDARD), GOOD_HEADERS))
    with pytest.raises(oh.OutputRefused, match="not an empty directory"):
        oh.build(out, url=URL, page_url=PAGE, transport=transport, sleep=lambda s: None)
    assert (out / "keep.txt").read_text(encoding="utf-8") == "someone's file"
    assert transport.calls == [], "nothing was fetched for a destination that was refused"


def test_a_leftover_partial_is_refused_before_any_fetch_and_not_removed(tmp_path):
    partial = tmp_path / "out.partial"
    partial.mkdir()
    (partial / "stale.txt").write_text("from an earlier run", encoding="utf-8")
    transport = FakeTransport(resp(200, oghist(STANDARD), GOOD_HEADERS))
    with pytest.raises(oh.OutputRefused, match="did not finish"):
        oh.build(tmp_path / "out", url=URL, page_url=PAGE, transport=transport, sleep=lambda s: None)
    assert transport.calls == []
    assert (partial / "stale.txt").read_text(encoding="utf-8") == "from an earlier run"


def test_a_missing_parent_is_refused_and_not_created(tmp_path):
    out = tmp_path / "no-such-parent" / "out"
    transport = FakeTransport()
    with pytest.raises(oh.OutputRefused, match="does not exist"):
        oh.build(out, url=URL, page_url=PAGE, transport=transport, sleep=lambda s: None)
    assert not (tmp_path / "no-such-parent").exists()
    assert transport.calls == []


def test_an_empty_existing_destination_is_allowed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    build_net(tmp_path, oghist(STANDARD))
    assert (out / "manifest.json").is_file()


def test_directories_are_fsynced_bottom_up_then_the_parent_after_the_rename(tmp_path, monkeypatch):
    out = tmp_path / "out"
    partial = tmp_path / "out.partial"
    calls = []
    real = oh._fsync_dir

    def recording(path):
        calls.append({"path": Path(path), "partial_exists": partial.exists(), "out_exists": out.exists()})
        real(path)

    monkeypatch.setattr(oh, "_fsync_dir", recording)
    build_net(tmp_path, oghist(STANDARD))
    paths = [c["path"] for c in calls]
    assert paths[-1] == tmp_path, "the parent is fsynced last"
    assert calls[-1]["out_exists"] is True and calls[-1]["partial_exists"] is False
    before = paths[:-1]
    assert set(before) == {partial, partial / "raw", partial / "source-page"}, \
        "every directory holding a file is fsynced before the rename"
    assert [len(p.parts) for p in before] == sorted([len(p.parts) for p in before], reverse=True), "deepest first"
    assert all(c["partial_exists"] and not c["out_exists"] for c in calls[:-1])


def test_an_error_before_the_rename_publishes_no_final_directory(tmp_path, monkeypatch):
    def fail(_root):
        raise OSError(5, "simulated failure before the rename")

    monkeypatch.setattr(oh, "_fsync_tree_bottom_up", fail)
    with pytest.raises(OSError):
        build_net(tmp_path, oghist(STANDARD))
    assert not (tmp_path / "out").exists()
    assert (tmp_path / "out.partial").is_dir(), "the partial is kept; nothing is removed automatically"


def test_an_error_after_the_rename_reports_durability_unconfirmed(tmp_path, monkeypatch, capsys):
    real = oh._fsync_dir

    def fail_on_parent(path):
        if Path(path) == tmp_path:
            raise OSError(5, "simulated I/O error")
        real(path)

    monkeypatch.setattr(oh, "_fsync_dir", fail_on_parent)
    with pytest.raises(oh.DurabilityUnconfirmed):
        build_net(tmp_path, oghist(STANDARD))
    assert (tmp_path / "out" / "manifest.json").is_file()


def test_main_returns_3_when_durability_is_unconfirmed(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise oh.DurabilityUnconfirmed("the parent fsync failed")

    monkeypatch.setattr(oh, "build", fail)
    assert oh.main(["--url", URL, "--page-url", PAGE, "--out", str(tmp_path / "x")]) == 3


# --- the command line ---------------------------------------------------------------------


def test_main_fetches_and_prints_the_manifest_sha(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(oh, "_open_over_network", FakeTransport(resp(200, oghist(STANDARD), GOOD_HEADERS)))
    out = tmp_path / "out"
    assert oh.main(["--url", URL, "--page-url", PAGE, "--out", str(out)]) == 0
    expected = hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()
    assert f"manifest sha256: {expected}" in capsys.readouterr().out.splitlines()


def test_main_refuses_a_layout_it_cannot_read_with_exit_1(tmp_path, monkeypatch, capsys):
    body = oghist(STANDARD, row5="Fiscal year:")
    monkeypatch.setattr(oh, "_open_over_network", FakeTransport(resp(200, body, GOOD_HEADERS)))
    assert oh.main(["--url", URL, "--page-url", PAGE, "--out", str(tmp_path / "x")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and "B5 is" in err, "refused for the header"
    assert not (tmp_path / "x").exists()
    assert (tmp_path / "x.partial" / "raw" / NAME).read_bytes() == body, "the bytes are kept as evidence"


def test_the_page_url_and_the_url_are_both_required_on_the_command_line(tmp_path):
    with pytest.raises(SystemExit):
        oh.main(["--url", URL, "--out", str(tmp_path / "x")])
    with pytest.raises(SystemExit):
        oh.main(["--page-url", PAGE, "--out", str(tmp_path / "x")])


# --- the source page -----------------------------------------------------------------------


def test_the_source_page_is_saved_and_its_sha_is_in_the_manifest(tmp_path):
    out, _sha, transport = build_net(tmp_path, oghist(STANDARD))
    assert (out / PAGE_FILE).read_bytes() == GOOD_PAGE_HTML
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    page = manifest["source"]["page"]
    assert page["sha256"] == hashlib.sha256(GOOD_PAGE_HTML).hexdigest()
    assert page["file"] == PAGE_FILE and page["links_resource"] is True and page["searched_for"] == URL
    assert transport.calls == [PAGE, URL], "one GET for the page, then one for the workbook"


def test_a_page_that_does_not_link_the_workbook_stops_the_run_before_the_workbook_is_requested(tmp_path):
    t = FakeTransport(resp(200, oghist(STANDARD), GOOD_HEADERS),
                      page=resp(200, b"<html>no link here</html>", GOOD_PAGE_HEADERS, final_url=PAGE))
    with pytest.raises(oh.SourceRefused, match="does not link"):
        oh.build(tmp_path / "out", url=URL, page_url=PAGE, transport=t, sleep=lambda s: None)
    assert t.calls == [PAGE]
    assert not (tmp_path / "out").exists()


def test_a_page_that_is_not_html_is_refused_before_the_workbook_is_requested(tmp_path):
    t = FakeTransport(resp(200, oghist(STANDARD), GOOD_HEADERS),
                      page=resp(200, GOOD_PAGE_HTML, {"content-type": "application/json"}, final_url=PAGE))
    with pytest.raises(oh.SourceRefused, match="not HTML"):
        oh.build(tmp_path / "out", url=URL, page_url=PAGE, transport=t, sleep=lambda s: None)
    assert t.calls == [PAGE]


def test_a_page_request_that_fails_stops_the_run_before_the_workbook(tmp_path):
    t = FakeTransport(resp(200, oghist(STANDARD), GOOD_HEADERS), page=resp(404))
    with pytest.raises(oh.FetchError, match="HTTP 404"):
        oh.build(tmp_path / "out", url=URL, page_url=PAGE, transport=t, sleep=lambda s: None)
    assert t.calls == [PAGE]


def test_a_non_https_page_is_refused_before_any_request(tmp_path):
    t = FakeTransport()
    with pytest.raises(oh.FetchError, match="not an https URL"):
        oh.build(tmp_path / "out", url=URL, page_url="http://datahelpdesk.example/page",
                 transport=t, sleep=lambda s: None)
    assert t.calls == []


def test_the_real_transport_is_never_reached_by_these_tests(tmp_path):
    # The autouse guard fails this test if the real opener is used. Calling it directly proves the guard holds.
    with pytest.raises(AssertionError, match="network path"):
        oh.urllib.request.build_opener()


def test_no_network_import_in_this_file():
    source = Path(__file__).read_text(encoding="utf-8")
    banned = {"urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp", "urllib3"}
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert name.split(".")[0] not in banned, f"network import {name} in a test"
