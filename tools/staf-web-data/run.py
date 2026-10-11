"""The STAF static web app's data (the browser layout of the releases; ``webdata/``).

    python tools/staf-web-data/run.py build [--out D:\\Data\\staf-web\\site] [--regions 0108 0710]
    C:\\Users\\gtmen\\anaconda3\\python.exe tools/staf-web-data/run.py upload --repo gtmenichino/staf-data

``build`` needs pyarrow (the repo .venv); ``upload`` needs huggingface_hub and ``hf auth login``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--out", default=None)
    p.add_argument("--bundle", default=None, help="the unpacked STAF data bundle (default apps/hr-data/data)")
    p.add_argument("--xs", default=None, help="the packed cross-section release (default D:\\Data\\xs-grid\\release)")
    p.add_argument("--library", default=None,
                   help="the downloaded library release files (default D:\\Data\\staf-web\\library-release)")
    p.add_argument("--regions", nargs="*", default=None, help="only these regions' files (the core always)")
    p = sub.add_parser("upload")
    p.add_argument("--site", default=r"D:\Data\staf-web\site")
    p.add_argument("--repo", required=True, help="the Hugging Face dataset, e.g. gtmenichino/staf-data")
    p.add_argument("--private", action="store_true")
    p.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        from webdata import build
        build.build(Path(args.out) if args.out else build.OUT, bundle=Path(args.bundle) if args.bundle else build.BUNDLE,
                    xs_release=Path(args.xs) if args.xs else build.XS_RELEASE,
                    library=Path(args.library) if args.library else build.LIBRARY, regions=args.regions)
    elif args.cmd == "upload":
        from webdata import hf
        hf.upload(Path(args.site), args.repo, private=args.private, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
