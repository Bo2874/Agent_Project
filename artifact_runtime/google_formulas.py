"""Google formula policy shared by native JSON and sandboxed XLSX inspection."""

import re
import zipfile
from urllib.parse import urlsplit

from defusedxml import ElementTree

from artifact_runtime.google_builtin_functions import BUILTIN_FUNCTIONS

POLICY_VERSION = "google-formulas-1"
NETWORK_FUNCTIONS = frozenset(
    {
        "IMPORTDATA",
        "IMPORTXML",
        "IMPORTHTML",
        "IMPORTRANGE",
        "IMPORTFEED",
        "GOOGLEFINANCE",
        "GOOGLETRANSLATE",
        "DETECTLANGUAGE",
        "IMAGE",
        "AI",
        "GEMINI",
    }
)
QUOTED = re.compile(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'')
DOUBLE_QUOTED = re.compile(r'"(?:[^"]|"")*"')
FUNCTION = re.compile(r"([^\W\d][\w.]*)\s*\(")


def validate_formula(formula):
    """Accept catalogued builtins, not bound scripts/named functions or network functions.

    Does not import Office libraries: native JSON validation runs on the host.
    Google still validates expression syntax and argument types before applying its batch.
    """
    if not isinstance(formula, str) or len(formula) > 50000:
        raise ValueError("Google formula exceeds supported size")
    reference_text = DOUBLE_QUOTED.sub(lambda match: " " * len(match.group()), formula)
    if re.search(r"\[[^\]]+\][^!]*!", reference_text):
        raise ValueError("External workbook references cannot be converted to Google")
    executable = QUOTED.sub(lambda match: " " * len(match.group()), formula)
    if "|" in executable:
        raise ValueError("Executable spreadsheet references are not permitted")
    for match in FUNCTION.finditer(executable):
        name = re.sub(r"^(?:_XLFN\.|_XLWS\.)+", "", match[1].upper())
        if name not in BUILTIN_FUNCTIONS or name in NETWORK_FUNCTIONS:
            raise ValueError("Unsupported Google function: " + name[:80] + "; compute in Python")
        if executable[: match.start()].rstrip().endswith("!"):
            raise ValueError("External spreadsheet function qualifiers are not permitted")
        if name == "HYPERLINK":
            target = re.match(r'\s*"((?:[^"]|"")*)"', formula[match.end() :])
            if target is None:
                raise ValueError("Hyperlinks require a literal safe target")
            url = target[1].replace('""', '"')
            if not url.startswith("#") and urlsplit(url).scheme.lower() not in {
                "http",
                "https",
                "mailto",
            }:
                raise ValueError("Unsafe Google hyperlink target")


def validate_native_xlsx(path):
    """Called only inside the isolated inspector, never by a backend upload handler."""
    from artifact_runtime.file_inspection import safe_office

    safe_office(path)
    formula_tags = {
        "f",
        "formula",
        "formula1",
        "formula2",
        "definedName",
        "calculatedColumnFormula",
        "totalsRowFormula",
    }
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("xl/") or not name.endswith(".xml"):
                continue
            with archive.open(name) as source:
                for _, node in ElementTree.iterparse(source, events=("end",)):
                    if node.tag.rsplit("}", 1)[-1] in formula_tags and node.text:
                        validate_formula(node.text)
                    node.clear()
