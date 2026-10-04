"""An xlsx file as its parts: relationships, content types, and a writer that keeps every part it
was not asked to change exactly as it was."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from typing import Optional
from xml.sax.saxutils import escape

CONTENT_TYPES = "[Content_Types].xml"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFDOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
R_OFFICE_DOCUMENT = OFFDOC + "/officeDocument"
R_WORKSHEET = OFFDOC + "/worksheet"
R_DRAWING = OFFDOC + "/drawing"
R_CHART = OFFDOC + "/chart"
R_CALC_CHAIN = OFFDOC + "/calcChain"
R_PRINTER = OFFDOC + "/printerSettings"
CT_WORKSHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
CT_DRAWING = "application/vnd.openxmlformats-officedocument.drawing+xml"
CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_ATTR = re.compile(r'([A-Za-z_][\w:.-]*)\s*=\s*"([^"]*)"')
_DATE = (1980, 1, 1, 0, 0, 0)


def attrs_of(tag_text: str) -> dict:
    """Attributes of one start tag, unescaped, in order."""
    from xml.sax.saxutils import unescape
    return dict((k, unescape(v, {"&quot;": '"', "&apos;": "'"})) for k, v in _ATTR.findall(tag_text))


def attr(value) -> str:
    return escape(str(value), {'"': "&quot;"})


@dataclass
class Rel:
    id: str
    type: str
    target: str
    mode: Optional[str] = None


class Package:
    """The parts of an xlsx file, in their original order."""

    def __init__(self, data: bytes):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.infos = dict((i.filename, i) for i in zf.infolist())
            self.order = [i.filename for i in zf.infolist()]
            self.parts = dict((name, zf.read(name)) for name in self.order)
        self.changed: set = set()

    # ------------------------------------------------------------------ parts
    def has(self, name: str) -> bool:
        return name in self.parts

    def get(self, name: str) -> bytes:
        return self.parts[name]

    def text(self, name: str) -> str:
        data = self.parts[name]
        return data.decode("utf-8-sig")

    def set(self, name: str, data: bytes) -> None:
        if name not in self.parts:
            self.order.append(name)
        self.parts[name] = data
        self.changed.add(name)

    def set_text(self, name: str, text: str) -> None:
        self.set(name, text.encode("utf-8"))

    def add(self, name: str, data: bytes, content_type: Optional[str] = None) -> None:
        assert name not in self.parts, name
        self.set(name, data)
        if content_type and self.content_type(name) != content_type:
            self.set_override(name, content_type)

    def remove(self, name: str) -> None:
        if name in self.parts:
            del self.parts[name]
            self.order.remove(name)
            self.changed.add(name)
        self.remove_override(name)
        rels = self.rels_part(name)
        if rels in self.parts:
            self.remove(rels)

    def unique(self, pattern: str) -> str:
        """The first free name for ``pattern`` (``"xl/worksheets/sheet{}.xml"``)."""
        n = 1
        while pattern.format(n) in self.parts:
            n += 1
        return pattern.format(n)

    # ------------------------------------------------------------------ relationships
    @staticmethod
    def rels_part(part: str) -> str:
        folder, name = posixpath.split(part)
        return posixpath.join(folder, "_rels", name + ".rels")

    def rels(self, part: str) -> list:
        name = self.rels_part(part)
        if name not in self.parts:
            return []
        out = []
        for tag in re.findall(r"<Relationship\b[^>]*?/?>", self.text(name)):
            a = attrs_of(tag)
            out.append(Rel(a.get("Id"), a.get("Type"), a.get("Target"), a.get("TargetMode")))
        return out

    def write_rels(self, part: str, rels: list) -> None:
        body = "".join(
            f'<Relationship Id="{attr(r.id)}" Type="{attr(r.type)}" Target="{attr(r.target)}"'
            + (f' TargetMode="{attr(r.mode)}"' if r.mode else "") + "/>" for r in rels)
        self.set_text(self.rels_part(part), XML_DECL + f'<Relationships xmlns="{NS_REL}">{body}</Relationships>')
        if self.content_type(self.rels_part(part)) is None:
            self.set_override(self.rels_part(part), "application/vnd.openxmlformats-package.relationships+xml")

    @staticmethod
    def resolve(part: str, target: str) -> str:
        """A relationship target as a part name."""
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))

    @staticmethod
    def relative(from_part: str, to_part: str) -> str:
        return posixpath.relpath(to_part, posixpath.dirname(from_part) or ".")

    @staticmethod
    def next_rel_id(rels: list) -> str:
        used = set(r.id for r in rels)
        n = 1
        while f"rId{n}" in used:
            n += 1
        return f"rId{n}"

    # ------------------------------------------------------------------ content types
    def _ct(self) -> str:
        return self.text(CONTENT_TYPES)

    def content_type(self, name: str) -> Optional[str]:
        ct = self._ct()
        for tag in re.findall(r"<Override\b[^>]*?/?>", ct):
            a = attrs_of(tag)
            if a.get("PartName", "").lstrip("/") == name:
                return a.get("ContentType")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        for tag in re.findall(r"<Default\b[^>]*?/?>", ct):
            a = attrs_of(tag)
            if a.get("Extension", "").lower() == ext:
                return a.get("ContentType")
        return None

    def set_override(self, name: str, content_type: str) -> None:
        self.remove_override(name)
        ct = self._ct()
        tag = f'<Override PartName="/{attr(name)}" ContentType="{attr(content_type)}"/>'
        self.set_text(CONTENT_TYPES, ct.replace("</Types>", tag + "</Types>"))

    def remove_override(self, name: str) -> None:
        ct = self._ct()
        pattern = re.compile(r'<Override\b[^>]*PartName="/?' + re.escape(attr(name)) + r'"[^>]*/>')
        new = pattern.sub("", ct)
        if new != ct:
            self.set_text(CONTENT_TYPES, new)

    # ------------------------------------------------------------------ writing
    def write(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in self.order:
                info = self.infos.get(name)
                if info is None or name in self.changed:
                    new = zipfile.ZipInfo(name, date_time=info.date_time if info else _DATE)
                    new.compress_type = zipfile.ZIP_DEFLATED
                    new.external_attr = info.external_attr if info else 0o600 << 16
                    zf.writestr(new, self.parts[name])
                else:
                    zf.writestr(info, self.parts[name])
        return buf.getvalue()
