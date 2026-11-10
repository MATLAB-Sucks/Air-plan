"""Writes VERSIONS.md from src/aircraft_foundry/versions.json (the app shows the same notes)."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = json.loads((root / "src" / "aircraft_foundry" / "versions.json").read_text())
out = ["# Aircraft Foundry versions", "",
       "What each version does, and the problems known in it (with where they were fixed). The app shows "
       "the same notes in its update banner and **Help → Version history**.", ""]
for v in data:
    tag = " *(not published)*" if v.get("unreleased") else ""
    out += [f"## {v['version']}{tag} — {v['date']}", "", f"**{v['summary']}**", "", "What it does:", ""]
    out += [f"- {c}" for c in v["changes"]] + ["", "Known problems:", ""]
    out += [f"- {p}" for p in v["problems"]] or ["- None known."]
    out.append("")
(root / "VERSIONS.md").write_text("\n".join(out))
print(f"wrote VERSIONS.md ({len(data)} versions)")
