"""The EASI authoring project: what StreamCurves edits, saves and publishes for EASI.

An EASI project holds the method files byte for byte (the consumer projection
EASI loads), plus the authoring record around them: lineage, the candidate
register and decisions, evidence references, fit recipes, author notes, the
preview case set and the edit history. Only the method files (and a calculator
generated from them) reach EASI; everything else stays with the author.

On disk it is a format-2 StreamCurves project (``project_file``) whose zip holds::

    easi/package.json        meta, lineage, register, decisions, evidence, recipes, notes, history
    easi/method/<file>       the eight method files, exact bytes
    easi/calculator/<file>   optional calculator generated for these files
    easi/cases.json          the preview case set (inputs only)
    easi/base/<file>         the origin's bytes of each method file this draft changed

The origin (the version a draft was forked from, or the import) is always recoverable:
the current files with the ``base`` bytes laid over them, checked against the origin's
package digest in the lineage. Unchanged files are never stored twice.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from .._vendor.easi import method_package as mp

PACKAGE_PART = "easi/package.json"
METHOD_PREFIX = "easi/method/"
CALCULATOR_PREFIX = "easi/calculator/"
CASES_PART = "easi/cases.json"
BASE_PREFIX = "easi/base/"
PROJECT_SCHEMA = 1
ASSESSMENT_TYPE = "easi"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@functools.lru_cache(maxsize=24)
def parsed(blob: bytes):
    """A method file's JSON, parsed once per content. Shared: read it, never change it
    (the edit functions parse their own copy)."""
    return json.loads(blob.decode("utf-8"))


@dataclass
class EasiProject:
    """One EASI method version being authored (or opened for review)."""

    meta: dict
    files: dict[str, bytes]
    calculator: Optional[tuple[str, bytes]] = None
    register: dict = field(default_factory=lambda: {"candidates": [], "decisions": []})
    evidence: list = field(default_factory=list)
    recipes: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    cases: Optional[dict] = None
    #: The origin's bytes of each method file this project changed (name -> bytes).
    base: dict = field(default_factory=dict)

    # identities --------------------------------------------------------------
    @property
    def package_digest(self) -> str:
        return mp.package_digest({n: sha(b) for n, b in self.files.items()})

    def method_version(self) -> str:
        return mp.method_version_for(self.meta.get("criteriaSet") or "regional", self.files)

    def identity(self) -> dict:
        return {"methodVersion": self.method_version(), "packageDigest": self.package_digest,
                "evaluatorDigest": mp.evaluator_digest()}

    def is_unchanged_from_origin(self) -> bool:
        origin = (self.meta.get("lineage") or {}).get("origin") or {}
        return origin.get("packageDigest") == self.package_digest

    def origin(self) -> dict:
        return dict((self.meta.get("lineage") or {}).get("origin") or {})

    def is_revision(self) -> bool:
        """A draft revision (forked from a version): the only kind of project whose
        method may be edited. An import or an opened library version is the method
        as it was, kept unchanged."""
        return self.origin().get("kind") == "revision"

    def origin_files(self) -> dict[str, bytes]:
        """The origin's method files: these files with the stored base bytes over them."""
        return {**self.files, **self.base}

    def origin_verified(self) -> bool:
        """True when the recovered origin files are exactly the origin the lineage names."""
        want = self.origin().get("packageDigest")
        got = mp.package_digest({n: sha(b) for n, b in self.origin_files().items()})
        return bool(want) and got == want

    def set_file(self, name: str, blob: bytes) -> None:
        """Replace one method file, keeping the origin's bytes the first time it changes
        and forgetting them once the file is back to exactly its origin bytes."""
        if name not in self.files:
            raise KeyError(name)
        if name not in self.base and self.files[name] != blob:
            self.base[name] = self.files[name]
        self.files[name] = blob
        if self.base.get(name) == blob:
            del self.base[name]

    def copy(self) -> "EasiProject":
        return EasiProject(meta=copy.deepcopy(self.meta), files=dict(self.files),
                           calculator=self.calculator, register=copy.deepcopy(self.register),
                           evidence=copy.deepcopy(self.evidence), recipes=copy.deepcopy(self.recipes),
                           notes=copy.deepcopy(self.notes), history=copy.deepcopy(self.history),
                           cases=self.cases, base=dict(self.base))

    # catalog views -----------------------------------------------------------
    # Read-only views, parsed once per content (see ``parsed``).
    def catalog(self) -> dict:
        return parsed(self.files["screening-methods.json"])

    def curves(self) -> dict:
        return parsed(self.files["reference-curves.json"])

    def metrics(self) -> dict:
        return parsed(self.files["easi-metrics.json"])

    # parts ---------------------------------------------------------------------
    def to_parts(self) -> dict[str, bytes]:
        doc = {"schema": PROJECT_SCHEMA, "assessmentType": ASSESSMENT_TYPE, "meta": self.meta,
               "identity": self.identity(), "register": self.register, "evidence": self.evidence,
               "recipes": self.recipes, "notes": self.notes, "history": self.history,
               "files": {n: {"bytes": len(b), "sha256": sha(b)} for n, b in sorted(self.files.items())},
               "calculator": ({"name": self.calculator[0], "bytes": len(self.calculator[1]),
                               "sha256": sha(self.calculator[1])} if self.calculator else None),
               "base": {n: {"bytes": len(b), "sha256": sha(b)} for n, b in sorted(self.base.items())}}
        parts = {PACKAGE_PART: json.dumps(doc, indent=1, sort_keys=True).encode("utf-8") + b"\n"}
        for name, blob in self.files.items():
            parts[METHOD_PREFIX + name] = blob
        if self.calculator:
            parts[CALCULATOR_PREFIX + self.calculator[0]] = self.calculator[1]
        if self.cases is not None:
            parts[CASES_PART] = json.dumps(self.cases, sort_keys=True).encode("utf-8")
        for name, blob in self.base.items():
            parts[BASE_PREFIX + name] = blob
        return parts

    @classmethod
    def from_parts(cls, parts: dict[str, bytes]) -> "EasiProject":
        if PACKAGE_PART not in parts:
            raise ValueError("not an EASI project (no easi/package.json)")
        doc = json.loads(parts[PACKAGE_PART].decode("utf-8"))
        if doc.get("assessmentType") != ASSESSMENT_TYPE:
            raise ValueError("easi/package.json is not an EASI project")
        if int(doc.get("schema") or 0) > PROJECT_SCHEMA:
            raise ValueError("this EASI project was saved by a newer StreamCurves; update the app")
        files = {}
        for name, rec in (doc.get("files") or {}).items():
            blob = parts.get(METHOD_PREFIX + name)
            if blob is None or len(blob) != rec.get("bytes") or sha(blob) != rec.get("sha256"):
                raise ValueError(f"EASI project method file damaged or missing: {name}")
            files[name] = blob
        missing = [n for n in mp.METHOD_FILES if n not in files]
        if missing:
            raise ValueError(f"EASI project lacks method files: {missing}")
        calculator = None
        crec = doc.get("calculator")
        if crec:
            blob = parts.get(CALCULATOR_PREFIX + crec["name"])
            if blob is not None and sha(blob) == crec.get("sha256"):
                calculator = (crec["name"], blob)
        cases = json.loads(parts[CASES_PART].decode("utf-8")) if CASES_PART in parts else None
        base = {}
        for name, rec in (doc.get("base") or {}).items():
            blob = parts.get(BASE_PREFIX + name)
            if name not in files or blob is None or sha(blob) != rec.get("sha256"):
                raise ValueError(f"EASI project origin copy damaged or missing: {name}")
            base[name] = blob
        return cls(meta=doc.get("meta") or {}, files=files, calculator=calculator,
                   register=doc.get("register") or {"candidates": [], "decisions": []},
                   evidence=doc.get("evidence") or [], recipes=doc.get("recipes") or {},
                   notes=doc.get("notes") or {}, history=doc.get("history") or [], cases=cases,
                   base=base)
