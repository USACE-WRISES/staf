"""Sheet references inside formulas: found by spans and rewritten in place.

Only the ``Sheet!`` prefixes a formula carries are touched; every other character of the formula
is copied unchanged. String literals and error literals are skipped. Anything this scanner does
not recognise (external or structured references, 3-D references) raises :class:`UnsupportedFormula`
so a workbook is refused rather than guessed at. Tested against openpyxl's formula tokenizer on
every formula of the three STAF templates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = re.compile(r"[A-Za-z_\\À-￿][A-Za-z0-9_.À-￿]*")
_ERRORS = re.compile(r"#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|GETTING_DATA|SPILL!|CALC!|FIELD!|BLOCKED!|"
                     r"CONNECT!|BUSY!|UNKNOWN!)", re.I)
_PLAIN_SHEET = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*$")
_LOOKS_LIKE_REF = re.compile(r"(?:[A-Za-z]{1,3}\d+|[Rr]\d*[Cc]\d*|[Rr]\d*|[Cc]\d*|TRUE|FALSE)$", re.I)


class UnsupportedFormula(ValueError):
    """A formula construct the rewriter does not handle."""


@dataclass(frozen=True)
class Span:
    start: int
    end: int            # exclusive, after the "!"
    sheet: str
    quoted: bool = False


def sheet_spans(formula: str) -> list:
    """The ``Sheet!`` prefixes in ``formula`` (without the leading "=", or with it)."""
    f, n, i, out = formula, len(formula), 0, []
    while i < n:
        c = f[i]
        if c == '"':                                       # string literal, "" escapes
            i += 1
            while i < n:
                if f[i] == '"':
                    if i + 1 < n and f[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == "'":                                       # quoted sheet name, '' escapes
            j, name = i + 1, []
            while j < n:
                if f[j] == "'":
                    if j + 1 < n and f[j + 1] == "'":
                        name.append("'")
                        j += 2
                        continue
                    break
                name.append(f[j])
                j += 1
            if j + 1 >= n or f[j + 1] != "!":
                raise UnsupportedFormula(f"quoted text that is not a sheet reference: {formula}")
            sheet = "".join(name)
            if ":" in sheet:
                raise UnsupportedFormula(f"3-D reference: {formula}")
            out.append(Span(i, j + 2, sheet, True))
            i = j + 2
            continue
        if c == "[":
            raise UnsupportedFormula(f"external or structured reference: {formula}")
        if c == "#":
            m = _ERRORS.match(f, i)
            i += len(m.group(0)) if m else 1
            continue
        if c.isdigit():                                    # a number or a row: never a sheet name
            while i < n and (f[i].isalnum() or f[i] in "._"):
                i += 1
            continue
        if c.isalpha() or c in "_\\" or ord(c) >= 0xC0:
            m = _IDENT.match(f, i)
            end = m.end()
            if end < n and f[end] == "!":
                out.append(Span(i, end + 1, f[i:end]))
                i = end + 1
                continue
            if end < n and f[end] == ":":
                m2 = _IDENT.match(f, end + 1)
                if m2 and m2.end() < n and f[m2.end()] == "!":
                    raise UnsupportedFormula(f"3-D reference: {formula}")
            i = end
            continue
        i += 1
    return out


def identifiers(formula: str) -> set:
    """Bare identifiers in ``formula`` that could be defined names: not a sheet prefix, not a
    function call, not inside a string or a quoted sheet name (lower-case)."""
    f, n, i, out = formula, len(formula), 0, set()
    while i < n:
        c = f[i]
        if c == '"':
            i += 1
            while i < n:
                if f[i] == '"':
                    if i + 1 < n and f[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if f[j] == "'":
                    if j + 1 < n and f[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 2
            continue
        if c.isdigit():
            while i < n and (f[i].isalnum() or f[i] in "._"):
                i += 1
            continue
        if c.isalpha() or c in "_\\" or ord(c) >= 0xC0:
            m = _IDENT.match(f, i)
            end = m.end()
            nxt = f[end] if end < n else ""
            prev = f[i - 1] if i > 0 else ""
            if nxt not in ("!", "(") and prev != "!":
                out.add(f[i:end].lower())
            i = end
            continue
        i += 1
    return out


def quote_sheet(name: str) -> str:
    """A sheet name as it must appear before "!" in a formula."""
    if _PLAIN_SHEET.match(name) and not _LOOKS_LIKE_REF.match(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def referenced_sheets(formula: str) -> set:
    return set(s.sheet.lower() for s in sheet_spans(formula))


def rewrite(formula: str, mapping: dict) -> str:
    """``formula`` with every reference to a sheet in ``mapping`` (lower-case keys) renamed."""
    spans = sheet_spans(formula)
    if not spans:
        return formula
    out, last = [], 0
    for s in spans:
        new = mapping.get(s.sheet.lower())
        if new is None:
            continue
        out.append(formula[last:s.start])
        # keep the template's style: a quoted reference stays quoted
        out.append(("'" + new.replace("'", "''") + "'" if s.quoted else quote_sheet(new)) + "!")
        last = s.end
    if last == 0:
        return formula
    out.append(formula[last:])
    return "".join(out)
