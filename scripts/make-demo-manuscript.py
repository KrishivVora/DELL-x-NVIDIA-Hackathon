#!/usr/bin/env python3
"""Regenerate demo-data/claimtrace-demo/manuscript.pdf: a 12-page synthetic
manuscript whose checkable claims sit on pages 4, 7 and 9, matching
tests/fixture-project/extracted/manuscript.txt and results.csv.

    .venv/bin/python scripts/make-demo-manuscript.py
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path("demo-data/claimtrace-demo/manuscript.pdf")

FILLER = (
    "This section describes background material and related work. It contains no "
    "quantitative claims and exists so the manuscript has realistic length. "
) * 6

PAGES = {
    1: "A Regularized Objective for Robust Image Classification\n\nAbstract\n\n"
       "We propose a regularized training objective and evaluate it on standard "
       "image classification benchmarks. Full results appear in Section 4.",
    4: "Methods\n\nWe evaluate our approach on five datasets spanning natural images, digits and\n"
       "apparel classification. All models share the same training schedule.\n\n"
       "Training runs for 50 epochs with a batch size of 128 on a single GPU.",
    7: "Results\n\nOur method improves accuracy by 12% over the baseline across all evaluated\n"
       "datasets. Gains are consistent and largest on SVHN.\n\n"
       "Table 2 reports per-dataset accuracy for the baseline and our method.",
    9: "Discussion\n\nThe approach is also more robust to input noise, which we attribute to the\n"
       "regularizing effect of the proposed objective.",
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for n in range(1, 13):
        page = doc.new_page()
        text = PAGES.get(n, f"Section {n}\n\n{FILLER}")
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), text, fontsize=11)
    doc.save(str(OUT))
    print(f"wrote {OUT} ({len(doc)} pages)")


if __name__ == "__main__":
    main()
