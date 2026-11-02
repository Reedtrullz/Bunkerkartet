"""Source-only checks of the O23 acceptance fixture's own parsing/argv logic.

These do not start the app, Chromium or the Linux namespaces; the native fixture is run by the
CI step that calls scripts/qa/o23_linux_admission.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/qa"))


def test_o23_matrix_and_result_parser():
    import o23_matrix
    o23_matrix.self_test()


def test_o23_acceptance_helpers():
    import o23_acceptance
    o23_acceptance.self_test()


def test_o23_linux_admission_argv():
    import o23_linux_admission
    o23_linux_admission.self_test()
