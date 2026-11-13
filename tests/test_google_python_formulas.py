import pytest
from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from artifact_runtime.google_formulas import validate_formula, validate_native_xlsx


@pytest.mark.parametrize(
    "formula",
    [
        "=SUM(A1:A5)",
        '=QUERY(A1:B10,"select A where B > 10",1)',
        "=MAP(A1:A5,LAMBDA(x,x*2))",
        '=IF(A1="IMPORTDATA(inside literal)",1,2)',
        "=SUM('My script (old)'!A1:A5)",
    ],
)
def test_google_builtin_formulas_and_passive_quoted_text(formula):
    validate_formula(formula)


@pytest.mark.parametrize("location", ["cell", "name", "validation"])
def test_native_conversion_validates_every_formula_location(tmp_path, location):
    book = Workbook()
    formula = 'IMPORTDATA("https://outside.example/data.csv")'
    if location == "cell":
        book.active["A1"] = "=" + formula
    elif location == "name":
        book.defined_names.add(DefinedName("unsafe", attr_text=formula))
    else:
        validation = DataValidation(type="custom", formula1=formula)
        validation.add("A1")
        book.active.add_data_validation(validation)
    path = tmp_path / "input.xlsx"
    book.save(path)
    with pytest.raises(ValueError):
        validate_native_xlsx(path)


def test_regular_builtin_workbook_can_be_converted(tmp_path):
    book = Workbook()
    book.active["A1"] = "=SUM(1,2)"
    path = tmp_path / "input.xlsx"
    book.save(path)
    validate_native_xlsx(path)


def test_unicode_custom_function_cannot_hide_builtin_suffix():
    with pytest.raises(ValueError):
        validate_formula("=éSUM(A1)")
