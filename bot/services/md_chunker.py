"""Нарезка справочников .md по заголовкам (ТЗ v2.1, раздел 3.2).

- режем по ## / ###, каждый фрагмент начинается с пути «Документ › Раздел › Подраздел»;
- цель 600–1 500 символов: длинный раздел делим по абзацам, короткие соседние склеиваем;
- таблица Markdown не разрезается никогда;
- блок «Как пользоваться (для бота)» в поиск не идёт — возвращается отдельно (rules).
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

MIN_CHARS = 600
MAX_CHARS = 1500
PATH_SEP = " › "

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_NUMBERING_RE = re.compile(r"^(?:БЛОК\s+)?\d+(?:\.\d+)*\.?\s+", re.IGNORECASE)
_BOT_RULES_RE = re.compile(r"как пользоваться.*для бота", re.IGNORECASE)


@dataclass
class ParsedDoc:
    meta: dict
    chunks: list[str]
    rules: str
    version_date: Optional[date] = None
    doc_type: str = "справочник"


@dataclass
class _Section:
    path: list[str]
    blocks: list[str] = field(default_factory=list)


def doc_title_from_filename(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1]
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem.replace("_", " ").strip()


def _clean_heading(text: str) -> str:
    text = text.replace("**", "").strip()
    return _NUMBERING_RE.sub("", text).strip() or text


def _split_front_matter(text: str) -> tuple[dict, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = {}
            for line in lines[1:i]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            return meta, "\n".join(lines[i + 1:])
    return {}, text


def _parse_version(value: str) -> Optional[date]:
    m = re.match(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _blocks(lines: list[str]) -> list[str]:
    """Абзацы, списки и таблицы. Заголовок ####+ приклеивается к следующему блоку."""
    blocks: list[str] = []
    cur: list[str] = []
    cur_is_table = False
    pending_heading: Optional[str] = None

    def flush() -> None:
        nonlocal cur, pending_heading
        if cur:
            text = "\n".join(cur).strip()
            if pending_heading:
                text = pending_heading + "\n" + text
                pending_heading = None
            blocks.append(text)
        cur = []

    for raw in lines:
        line = raw.rstrip()
        if line.strip() == "---":  # разделитель, смысла не несёт
            flush()
            continue
        is_table = line.lstrip().startswith("|")
        if not line.strip():
            if not cur_is_table:
                flush()
            continue
        if _HEADING_RE.match(line):
            flush()
            pending_heading = (pending_heading + "\n" if pending_heading else "") + line
            continue
        if cur and is_table != cur_is_table:
            flush()
        cur_is_table = is_table
        cur.append(line)
    flush()
    if pending_heading:
        blocks.append(pending_heading)
    return [b for b in blocks if b.strip()]


def _pack(blocks: list[str], limit: int) -> list[str]:
    """Делит блоки на куски ≤ limit примерно равной длины. Блок длиннее limit (таблица) идёт целиком."""
    total = sum(len(b) + 2 for b in blocks)
    n = max(1, -(-total // limit))
    target = total / n
    pieces: list[str] = []
    cur = ""
    for b in blocks:
        candidate = f"{cur}\n\n{b}" if cur else b
        if cur and (len(candidate) > limit or len(cur) >= target):
            pieces.append(cur)
            cur = b
        else:
            cur = candidate
    if cur:
        pieces.append(cur)
    return pieces


def _render(parts: list[tuple[list[str], str]]) -> str:
    """Склеенные части → «путь\n\nтекст». Подзаголовки ниже общего пути — жирной строкой."""
    paths = [p for p, _ in parts]
    common = paths[0]
    for p in paths[1:]:
        i = 0
        while i < min(len(common), len(p)) and common[i] == p[i]:
            i += 1
        common = common[:i]
    h2s = list(dict.fromkeys(p[1] for p in paths if len(p) > 1))
    path = common if len(common) > 1 or len(h2s) < 2 else common + [" · ".join(h2s)]
    body = []
    for p, text in parts:
        head = PATH_SEP.join(p[len(common):])
        body.append(f"**{head}**\n{text}" if head and len(parts) > 1 else text)
    return PATH_SEP.join(path) + "\n\n" + "\n\n".join(body)


def parse_markdown(text: str, filename: str) -> ParsedDoc:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    meta, body = _split_front_matter(text)
    root = doc_title_from_filename(filename)

    sections: list[_Section] = [_Section(path=[root])]
    rules_parts: list[str] = []
    in_rules = False
    h2: Optional[str] = None

    for line in body.split("\n"):
        m = _HEADING_RE.match(line)
        level = len(m.group(1)) if m else 0
        if m and level == 1:
            continue  # заголовок документа: в пути уже есть имя файла
        if m and level == 2:
            title = m.group(2)
            in_rules = bool(_BOT_RULES_RE.search(title))
            if in_rules:
                continue
            h2 = _clean_heading(title)
            sections.append(_Section(path=[root, h2]))
            continue
        if m and level == 3 and not in_rules:
            path = [root, h2, _clean_heading(m.group(2))] if h2 else [root, _clean_heading(m.group(2))]
            sections.append(_Section(path=path))
            continue
        if in_rules:
            rules_parts.append(line)
        else:
            sections[-1].blocks.append(line)

    # раздел → куски с запасом под строку пути
    raw: list[tuple[list[str], str, bool]] = []  # (путь, текст, раздел целиком)
    for s in sections:
        blocks = _blocks(s.blocks)
        if not blocks:
            continue
        budget = MAX_CHARS - len(PATH_SEP.join(s.path)) - 2
        pieces = _pack(blocks, budget)
        for p in pieces:
            raw.append((s.path, p, len(pieces) == 1))

    # склейка коротких соседних разделов (только разделы целиком, не хвосты длинных)
    groups: list[tuple[list[tuple[list[str], str]], bool]] = []
    for path, piece, whole in raw:
        if groups and whole and groups[-1][1]:
            parts = groups[-1][0]
            cur_len = len(_render(parts))
            new_len = len(_render(parts + [(path, piece)]))
            short = cur_len < MIN_CHARS or len(piece) + len(PATH_SEP.join(path)) + 2 < MIN_CHARS
            if short and new_len <= MAX_CHARS:
                parts.append((path, piece))
                continue
        groups.append(([(path, piece)], whole))

    chunks = [_render(parts) for parts, _ in groups]
    rules = "\n".join(rules_parts).strip()
    rules = "\n".join(l for l in rules.split("\n") if l.strip() != "---").strip()
    return ParsedDoc(
        meta=meta,
        chunks=chunks,
        rules=rules,
        version_date=_parse_version(meta.get("версия", "")),
        doc_type=meta.get("тип") or "справочник",
    )
