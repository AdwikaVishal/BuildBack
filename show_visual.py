import sys
from rippleback.scenarios import get_scenario
from rippleback.graph import build_graph

SID = sys.argv[1] if len(sys.argv) > 1 else "project-full"
g = build_graph(get_scenario(SID))

STATUS_CLASS = {
    "done": "done",
    "in_progress": "prog",
    "pending": "pend",
    "blocked": "bld",
}

lines = []
for u, v, attrs in g.edges(data=True):
    et = attrs.get("dependency_type", "requires_completion")
    if et == "benefits_from":
        lines.append(f'    {u} -.->|benefits_from| {v}')
    elif et == "requires_arrival_by":
        lines.append(f'    {u} -->|requires_arrival_by| {v}')
    else:
        lines.append(f'    {u} --> {v}')
edges_block = "\n".join(lines)

node_lines = []
for n, d in sorted(g.nodes(data=True)):
    st = d.get("status", "")
    cls = STATUS_CLASS.get(st, "pend")
    flags = []
    m = d.get("metadata", {})
    if m.get("external"):
        flags.append("EXT")
    if m.get("slack_minutes") is not None:
        flags.append(f"slack {m['slack_minutes']}m")
    if d.get("duration") is not None:
        flags.append(f"d={d['duration']:g}")
    extra = ("<br/>" + ", ".join(flags)) if flags else ""
    node_lines.append(f'    {n}["{n}<br/>({st}){extra}"]:::{cls}')
nodes_block = "\n".join(node_lines)

classdefs = """    classDef done fill:#d5f5d5,stroke:#2e7d32,color:#000;
    classDef prog fill:#d5e5ff,stroke:#1565c0,color:#000;
    classDef pend fill:#fff3cd,stroke:#f9a825,color:#000;
    classDef bld  fill:#ffd6d6,stroke:#c62828,color:#000;"""

html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Graph: __SID__</title>
<style>
  body { font-family: sans-serif; margin: 20px; background:#fafafa; }
  h1 { font-size: 18px; }
  .legend { font-size: 13px; margin-bottom: 12px; line-height: 1.7; }
  .legend .box { display: inline-block; width: 14px; height: 14px;
                 border-radius: 3px; vertical-align: middle; margin-right: 4px;
                 border: 1px solid #555; }
  svg { max-width: 100%; height: auto; }
</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({ startOnLoad: true, theme: "neutral",
    flowchart: { curve: "basis", nodeSpacing: 45, rankSpacing: 70 } });
</script>
</head>
<body>
<h1>__SID__ dependency graph</h1>
<div class="legend">
  <b>node fill = committed status:</b>
  <span class="box" style="background:#d5f5d5"></span>done
  <span class="box" style="background:#d5e5ff"></span>in_progress
  <span class="box" style="background:#fff3cd"></span>pending
  <span class="box" style="background:#ffd6d6"></span>blocked
  <br/><b>edges:</b> solid arrow = hard dependency &nbsp;|&nbsp;
  dotted arrow = benefits_from (soft) &nbsp;|&nbsp;
  arrow direction = prerequisite --> dependent
</div>
<pre class="mermaid">
flowchart TD
__NODES__
__EDGES__
__CLASSDEFS__
</pre>
</body>
</html>
"""

html = html.replace("__SID__", SID)
html = html.replace("__NODES__", nodes_block)
html = html.replace("__EDGES__", edges_block)
html = html.replace("__CLASSDEFS__", classdefs)

out = f"graph_{SID}.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("wrote " + out + " -> open it in a browser")

