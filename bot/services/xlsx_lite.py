"""Чтение .xlsx без зависимостей (zipfile + iterparse): нужные листы → списки строк (значения — строки).
Память держим низкой: лист разбирается потоком, разобранные строки сразу выбрасываются из дерева."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class XlsxError(ValueError):
    pass


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _shared(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out = []
    for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
        if el.tag == f"{_M}si":
            out.append("".join(t.text or "" for t in el.iter(f"{_M}t")))
            el.clear()
    return out


def sheet_names(data: bytes) -> list[str]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise XlsxError(f"не похоже на .xlsx: {e}") from e
    return [s.get("name") for s in wb.iter(f"{_M}sheet")]


def read_sheets(data: bytes, wanted: Iterable[str]) -> dict[str, list[list[str]]]:
    """{имя листа: строки}; листы, которых нет в файле, в ответ не попадают."""
    wanted = set(wanted)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise XlsxError(f"не похоже на .xlsx: {e}") from e
    ss: Optional[list[str]] = None
    out: dict[str, list[list[str]]] = {}
    for s in wb.iter(f"{_M}sheet"):
        name = s.get("name")
        if name not in wanted:
            continue
        if ss is None:
            ss = _shared(z)
        target = rels[s.get(_R)].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for _, el in ET.iterparse(z.open(target)):
            if el.tag != f"{_M}row":
                continue
            cells = {}
            for c in el.iter(f"{_M}c"):
                v, t = c.find(f"{_M}v"), c.get("t")
                if t == "s" and v is not None:
                    val = ss[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{_M}t"))
                else:
                    val = v.text if v is not None and v.text is not None else ""
                cells[_col(c.get("r"))] = val
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            el.clear()
        out[name] = rows
    return out
