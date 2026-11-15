"""Artwork for the project page (website/): docs/website/mark.svg and docs/website/hero.png.

    python3 tools/website_art.py

The hero is the README's dashboard picture (termstats.png, rendered from `termstats --demo`)
in a terminal frame on the left, with the mark, the name and the tagline bottom right - the
layout the product-page kit expects (motif left, room for the text on the right). Needs a
Chromium-family browser; set TERMSTATS_REPORT_BROWSER to its binary if it is not found.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BG = "#0c0d14"
W, H = 2912, 1440

MARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#1a1b26"/>
<rect x="12" y="15" width="40" height="8" rx="2" fill="#f7768e"/>
<rect x="12" y="28" width="29" height="8" rx="2" fill="#e0af68"/>
<rect x="12" y="41" width="18" height="8" rx="2" fill="#4fb3a5"/>
</svg>
"""

PAGE = """<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;background:{bg};overflow:hidden;
  font-family:-apple-system,"Helvetica Neue",Arial,sans-serif}}
.glow{{position:absolute;left:-200px;top:-300px;width:2300px;height:2000px;
  background:radial-gradient(closest-side,rgba(45,108,223,.20),rgba(45,108,223,0))}}
.warm{{position:absolute;left:900px;top:-200px;width:1300px;height:1100px;
  background:radial-gradient(closest-side,rgba(224,175,104,.10),rgba(224,175,104,0))}}
.term{{position:absolute;left:120px;top:210px;width:1560px;border-radius:26px;overflow:hidden;
  background:#1a1b26;border:2px solid #2c2e3f;box-shadow:0 60px 140px rgba(0,0,0,.6)}}
.bar{{height:62px;background:#16171f;display:flex;align-items:center;padding-left:28px;gap:16px}}
.bar i{{width:22px;height:22px;border-radius:50%;background:#3a3d52;display:block}}
.term img{{display:block;width:1520px;margin:14px 20px 20px}}
.fade{{position:absolute;left:1300px;top:0;width:1612px;height:{h}px;
  background:linear-gradient(90deg,rgba(12,13,20,0),{bg} 46%)}}
.brand{{position:absolute;right:150px;bottom:250px;display:flex;align-items:center;gap:54px}}
.brand svg{{width:200px;height:200px}}
.brand b{{display:block;font-size:150px;letter-spacing:-4px;color:#eef0f7;line-height:1}}
.brand span{{display:block;font-size:52px;color:#a9b1d6;margin-top:16px}}
</style><div class="glow"></div><div class="warm"></div>
<div class="term"><div class="bar"><i></i><i></i><i></i></div><img src="{shot}"></div>
<div class="fade"></div>
<div class="brand">{mark}<div><b>termstats</b><span>The machine, at a glance.</span></div></div>
"""


def browser():
    wanted = os.environ.get("TERMSTATS_REPORT_BROWSER")
    names = [wanted] if wanted else [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "google-chrome", "chromium", "chromium-browser"]
    for name in names:
        path = name if os.path.isabs(name) and os.path.exists(name) else shutil.which(name)
        if path:
            return path
    sys.exit("website_art: no Chromium-family browser found")


def main():
    (ROOT / "docs" / "website" / "mark.svg").write_text(MARK, encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "hero.html"
        page.write_text(PAGE.format(w=W, h=H, bg=BG, mark=MARK,
                                    shot=(ROOT / "termstats.png").as_uri()), encoding="utf-8")
        out = ROOT / "docs" / "website" / "hero.png"
        subprocess.run([browser(), "--headless=new", "--hide-scrollbars", "--allow-file-access-from-files",
                        f"--user-data-dir={tmp}/profile", f"--window-size={W},{H}",
                        f"--screenshot={out}", page.as_uri()], check=True, capture_output=True, timeout=120)
    print(f"website_art: docs/website/mark.svg, docs/website/hero.png ({W}x{H})")


if __name__ == "__main__":
    main()
