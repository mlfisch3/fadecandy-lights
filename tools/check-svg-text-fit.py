#!/usr/bin/env python3
"""Render-check the hand-written SVG diagrams for silent text overruns.

SVG text does not wrap and is not clipped: a label that grew one word too long
just draws straight through the border of the card it sits in, or off the
canvas, and nothing in the source looks wrong. This renders every diagram in
headless Chrome and measures real laid-out text boxes against the panel rects
they overlap, so the defect fails a command instead of a reviewer's eye.

Usage:  python3 tools/check-svg-text-fit.py [file.svg ...]
Exit 0 when every label sits inside its panel, 1 on any collision or on any
diagram that could not be measured, 2 when no Chrome binary is available to
render with. A diagram the probe failed to measure is a failure, never a pass:
an empty result and a clean result must not look alike.
"""

import glob
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)

# Measures each <text> against every stroked <rect> it overlaps, in the SVG's
# own user units, then reports the edges it straddles.
# The probe always writes a first line: "MEASURED <n>" once it has walked every
# text node, or "ERROR <message>" if it threw on the way. Silence means it never
# ran, which the Python side treats as a failure rather than as a clean result.
PROBE = r"""
<pre id="out"></pre>
<script>
(function () {
var lines = [], measured = 0;
try {
var svg = document.querySelector('svg');
if (!svg) throw new Error('no <svg> element in the rendered page');
function abs(el) {
  var b = el.getBBox();
  var m = svg.getScreenCTM().inverse().multiply(el.getScreenCTM());
  var corners = [[b.x, b.y], [b.x + b.width, b.y],
                 [b.x, b.y + b.height], [b.x + b.width, b.y + b.height]];
  var pts = corners.map(function (p) {
    var pt = svg.createSVGPoint(); pt.x = p[0]; pt.y = p[1];
    var q = pt.matrixTransform(m); return [q.x, q.y];
  });
  var xs = pts.map(function (p) { return p[0]; });
  var ys = pts.map(function (p) { return p[1]; });
  return {x0: Math.min.apply(null, xs), x1: Math.max.apply(null, xs),
          y0: Math.min.apply(null, ys), y1: Math.max.apply(null, ys)};
}
var rects = Array.prototype.map.call(svg.querySelectorAll('rect'), function (r) {
  var a = abs(r), cs = getComputedStyle(r);
  a.sw = parseFloat(cs.strokeWidth) || 0;
  a.stroke = cs.stroke;
  return a;
});
Array.prototype.forEach.call(svg.querySelectorAll('text'), function (t) {
  var a = abs(t), content = (t.textContent || '').trim();
  if (!content) return;
  measured += 1;
  rects.forEach(function (r) {
    if (r.stroke === 'none' || r.stroke === 'rgba(0, 0, 0, 0)') return;
    // Legend swatches and pin pads are smaller than any label; labels are
    // meant to sit beside them, so they are not panels to be contained by.
    if (r.x1 - r.x0 < 24 || r.y1 - r.y0 < 12) return;
    var pad = (r.sw || 1) / 2;
    if (!(a.y1 > r.y0 - pad && a.y0 < r.y1 + pad)) return;
    if (!(a.x1 > r.x0 - pad && a.x0 < r.x1 + pad)) return;
    function report(edge, amount) {
      lines.push('"' + content.slice(0, 64) + '" crosses the ' + edge +
        ' border of rect(' + r.x0.toFixed(0) + ',' + r.y0.toFixed(0) + ' ' +
        (r.x1 - r.x0).toFixed(0) + 'x' + (r.y1 - r.y0).toFixed(0) + ') by ' +
        amount.toFixed(2) + ' units');
    }
    if (a.x0 < r.x0 - pad && a.x1 > r.x0 + pad)
      report('left', Math.min(r.x0 - a.x0, a.x1 - r.x0));
    if (a.x0 < r.x1 - pad && a.x1 > r.x1 + pad) report('right', a.x1 - r.x1);
    if (a.y0 < r.y0 - pad && a.y1 > r.y0 + pad) report('top', a.y1 - r.y0);
    if (a.y0 < r.y1 - pad && a.y1 > r.y1 + pad) report('bottom', a.y1 - r.y1);
  });
  var vb = svg.viewBox.baseVal;
  if (a.x0 < vb.x - 0.5 || a.x1 > vb.x + vb.width + 0.5 ||
      a.y0 < vb.y - 0.5 || a.y1 > vb.y + vb.height + 0.5)
    lines.push('"' + content.slice(0, 64) + '" falls outside the viewBox');
});
lines.unshift('MEASURED ' + measured);
} catch (e) {
  lines = ['ERROR ' + ((e && e.message) ? e.message : String(e))];
}
document.getElementById('out').textContent = lines.join('\n');
})();
</script>
"""


class ProbeError(Exception):
    """The diagram could not be measured, so its result proves nothing."""


def find_chrome():
    for name in CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def text_node_count(svg_path):
    """How many non-empty <text> elements the file itself contains.

    This is what the probe is expected to measure. If the probe reports zero
    and this says otherwise, the probe did not do its job.
    """
    with open(svg_path, encoding="utf-8") as handle:
        source = handle.read()
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return len(re.findall(r"<text[\s>]", source))
    return sum(
        1
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1] == "text" and "".join(el.itertext()).strip()
    )


def collisions(chrome, svg_path):
    """Return the list of text/border collisions Chrome measures in one file.

    Each diagram is rendered on its own page: the SVGs share class names such
    as .warnt across files, so stacking them in one document lets a later
    stylesheet restyle an earlier drawing and mismeasures every label in it.
    """
    with open(svg_path, encoding="utf-8") as handle:
        source = handle.read()
    if source.lstrip().startswith("<?xml"):
        source = source.split("?>", 1)[1]
    page = ('<!doctype html><html><head><meta charset="utf-8">'
            "<style>body{margin:0}</style></head><body>" + source + PROBE +
            "</body></html>")
    with tempfile.TemporaryDirectory() as tmp:
        page_path = os.path.join(tmp, "probe.html")
        with open(page_path, "w", encoding="utf-8") as handle:
            handle.write(page)
        rendered = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             "--window-size=1180,1400", "--virtual-time-budget=4000",
             "--dump-dom", "file://" + page_path],
            capture_output=True, text=True, timeout=120,
        ).stdout
    found = re.search(r'<pre id="out">(.*?)</pre>', rendered, re.S)
    if not found:
        raise ProbeError("the probe element is missing from the rendered page")
    lines = html.unescape(found.group(1)).strip().splitlines()
    if not lines:
        raise ProbeError("the probe wrote no result, so nothing was measured")
    marker, rest = lines[0], lines[1:]
    if marker.startswith("ERROR "):
        raise ProbeError("the probe failed: " + marker[len("ERROR "):])
    if not marker.startswith("MEASURED "):
        raise ProbeError("the probe did not run to completion")
    measured = int(marker.split()[1])
    expected = text_node_count(svg_path)
    if measured == 0 and expected:
        raise ProbeError(f"measured 0 of the {expected} text nodes in this file")
    return rest


def main(argv):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = argv or sorted(glob.glob(os.path.join(here, "docs/diagrams/*.svg")))
    if not targets:
        raise SystemExit("no diagrams to check")
    chrome = find_chrome()
    if chrome is None:
        print("no Chrome or Chromium binary found; install one to run this check")
        return 2
    failed = False
    for path in targets:
        name = os.path.relpath(path, here)
        try:
            found = collisions(chrome, path)
        except ProbeError as error:
            failed = True
            print("FAIL " + name)
            print("       could not measure this diagram: " + str(error))
            continue
        if found:
            failed = True
            print("FAIL " + name)
            for line in found:
                print("       " + line)
        else:
            print("ok   " + name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
