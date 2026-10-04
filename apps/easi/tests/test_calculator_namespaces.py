"""The calculator's bytes do not depend on what the process imported before (2026-10-02).

lxml and ElementTree keep one namespace prefix registry per process. owslib, which pynhd imports,
registers ``dct`` for Dublin Core terms; imported after openpyxl, it made ``docProps/core.xml`` say
``dct:created`` and left the modified stamp unpinned, so the committed workbook no longer matched
the generator. ``build_calculator.openpyxl_namespaces`` puts openpyxl's prefixes back for the save
and the registry as it was afterwards. These tests register the conflicting prefixes directly
(importing pynhd here would change the registry for the rest of the session).
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from openpyxl import LXML
from openpyxl.xml.functions import register_namespace

import calculator_cases as cc

bc = cc.bc


def test_prefixes_another_library_registered_change_no_byte():
    dcterms, cp = bc.OPENPYXL_NAMESPACES["dcterms"], bc.OPENPYXL_NAMESPACES["cp"]
    register_namespace("dct", dcterms)                       # what owslib registers
    register_namespace("coreprops", cp)                      # any other library's choice
    try:
        ok, why = bc.check(str(cc.WORKBOOK))
        assert ok, why
        assert bc.registered_prefix(dcterms) == "dct"        # the build left the registry as it was
        assert bc.registered_prefix(cp) == "coreprops"
    finally:
        register_namespace("dcterms", dcterms)
        register_namespace("cp", cp)


@pytest.mark.skipif(not LXML, reason="openpyxl writes with ElementTree here; the probe reads lxml")
def test_the_prefixes_are_the_ones_openpyxl_registers():
    # a fresh interpreter that imported openpyxl only: its prefixes are the ones the build restores
    probe = ("import json, sys\n"
             "import openpyxl.xml.functions\n"
             "from lxml import etree\n"
             "uris = json.loads(sys.argv[1])\n"
             "print(json.dumps(dict((u, etree.Element('{%s}x' % u).prefix) for u in uris)))\n")
    uris = list(bc.OPENPYXL_NAMESPACES.values())
    out = subprocess.run([sys.executable, "-c", probe, json.dumps(uris)], capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == dict((uri, prefix) for prefix, uri in bc.OPENPYXL_NAMESPACES.items())
