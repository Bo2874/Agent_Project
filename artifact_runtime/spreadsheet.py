"""Office parsing is imported only inside the fixed sandbox image."""

import stat
import zipfile
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree


def preflight_workbook(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(item.file_size for item in entries) > 104857600:
            raise ValueError("Spreadsheet exceeds archive limits")
        names = set()
        for item in entries:
            name = item.filename
            if (
                name in names
                or name.startswith("/")
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or ":" in name
                or item.flag_bits & 1
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise ValueError("Unsafe spreadsheet archive member")
            names.add(name)
        if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
            raise ValueError("Not an XLSX workbook")
        unsupported = ("vbaproject", "externallinks/", "xl/media/", "xl/embeddings/", "xl/activex/")
        if any(any(part in name.lower() for part in unsupported) for name in names):
            raise ValueError(
                "Macros, external links, images and embedded objects are not supported"
            )
        # A relationship may target the network even without an externalLinks part.
        for name in names:
            if name.endswith(".rels") or name == "[Content_Types].xml":
                if archive.getinfo(name).file_size > 1048576:
                    raise ValueError("Spreadsheet relationship exceeds limit")
                root = ElementTree.fromstring(
                    archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True
                )
                if any(item.attrib.get("TargetMode", "").lower() == "external" for item in root):
                    raise ValueError("External spreadsheet relationships are not supported")
