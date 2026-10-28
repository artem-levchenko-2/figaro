"""What `figaro notes` prints: the designer's answer to the reviewer's notes.

The bridge sends the answer as data (bridge_review, `POST /notes`); this turns it
into the text an agent reads.
"""
from __future__ import annotations

import shlex

WAITED = ("The designer hasn't answered in 10 minutes. The reviewer's notes wait for them in the "
          "Figaro window. Finish your work; if they send notes later, you get them with figaro notes.")


def _n(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def render(resp, agent):
    """The lines for one answer of /notes."""
    state = resp.get("state")
    if state == "sent":
        notes = resp.get("notes") or []
        total, left = int(resp.get("total") or len(notes)), int(resp.get("left") or 0)
        lines = [f"The designer sent you {len(notes)} of the reviewer's {_n(total, 'note')}:", ""]
        for i, note in enumerate(notes, 1):
            head = f"{i}. "
            pad = " " * len(head)
            lines.append(f"{head}{note.get('title')}: {note.get('text')}")
            known = {l.get("id"): l for l in note.get("layers") or []}
            names = note.get("names")  # a 1.8.0 bridge names only the note's layers
            known.update({k: v for k, v in names.items() if isinstance(v, dict)} if isinstance(names, dict) else {})
            links = note.get("links") or {}
            if note.get("details"):
                # one line per distinct text, each with its own layers
                for d in note["details"]:
                    lines.append(f"{pad}- {d.get('text')}")
                    for lid in d.get("layers") or []:
                        layer = known.get(lid)
                        url = f"  {links[lid]}" if links.get(lid) else ""
                        who = (f"\"{layer.get('name')}\" ({layer.get('type')}) {lid}" if layer else str(lid))
                        lines.append(f"{pad}  {who}{url}")
                if note.get("detailsMore"):
                    lines.append(f"{pad}  and {_n(int(note['detailsMore']), 'more line')} like these")
            else:
                for layer in note.get("layers") or []:
                    url = f"  {layer['url']}" if layer.get("url") else ""
                    lines.append(f"{pad}\"{layer.get('name')}\" ({layer.get('type')}) {layer.get('id')}{url}")
                if note.get("more"):
                    lines.append(f"{pad}and {_n(int(note['more']), 'more layer')}")
        lines.append("")
        fix = {1: "Fix this one", 2: "Fix these two"}.get(len(notes), "Fix these")
        lines.append((f"They left {left} as it is. " if left else "")
                     + f"{fix}, then run figaro done again.")
        return lines
    if state == "fine":
        return ["The designer looked at the reviewer's notes: all fine, nothing to fix."]
    if state == "expired":
        return [WAITED]
    if state == "pending":
        return ["The designer hasn't answered yet. Wait for it: "
                f"figaro notes -A {shlex.quote(agent)} --wait   (up to 10 min)"]
    if state == "withdrawn":
        return ["The reviewer's notes for you were withdrawn (the reviewer was turned off, or it "
                "checked newer work). Finish your work."]
    return [f"No notes from the reviewer for {agent} in this file."]
