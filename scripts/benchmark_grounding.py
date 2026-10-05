"""Native OCR benchmark on a deterministic PNG; never desktop validation."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs",type=int,default=5)
    parser.add_argument("--output",default=".cache/grounding-benchmark.json")
    parser.add_argument("--image")
    args = parser.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from PIL import Image,ImageDraw,ImageFont
    from jarvis_agent.fast_grounding import bounded_detect,png_from_image
    if args.image:
        png = Path(args.image).read_bytes()
    else:
        image = Image.new("RGB",(900,250),"white")
        ImageDraw.Draw(image).text((30,40),"Generic Person - Search - Message",
            font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",34),fill="black")
        png = png_from_image(image)
    results = []
    for _ in range(max(1,args.runs)):
        started = time.perf_counter()
        result = bounded_detect(png,timeout_s=2)
        results.append({"wall_seconds":time.perf_counter()-started,**result})
    report = {"evidence_kind":"real_windows_ocr_on_fixture_not_desktop_acceptance",
              "runs":results,"max_seconds":max(r["wall_seconds"] for r in results)}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=True))
    return 0 if report["max_seconds"] < 2.5 and all(r["elements"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
