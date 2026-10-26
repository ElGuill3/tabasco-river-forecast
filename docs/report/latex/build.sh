#!/usr/bin/env bash
# Build informe.pdf from ../informe.md (single source).
# Needs pandoc and tectonic on PATH (or in /home/linuxbrew/.linuxbrew/bin).
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$PATH:/home/linuxbrew/.linuxbrew/bin"

python3 - <<'EOF'
import re
from pathlib import Path

lines = Path("../informe.md").read_text(encoding="utf-8").splitlines()
# Title, authors and date live on the LaTeX title page.
lines = lines[next(i for i, l in enumerate(lines) if l.startswith("## 1. ")):]
# The Markdown gives each figure a short alt text and a following
# "**Figura N.** ..." paragraph; LaTeX numbers figures itself, so that
# paragraph becomes the caption.
merged, i = [], 0
while i < len(lines):
    line = lines[i]
    m = re.match(r"^!\[[^\]]*\]\((\S+)\)$", line)
    if m and i + 2 < len(lines) and re.match(r"^\*\*Figura \d+\.\*\* ", lines[i + 2]):
        caption = re.sub(r"^\*\*Figura \d+\.\*\* ", "", lines[i + 2])
        merged.append(f"![{caption}]({m.group(1)})")
        i += 3
        continue
    merged.append(line)
    i += 1
lines = merged
out = []
for line in lines:
    if line.strip() == "---":
        continue
    # Keep the Markdown section numbers, which the text cites ("sección 4.4").
    if re.match(r"^#{2,4} \d", line):
        line += " {.unnumbered}"
    out.append(line)


# A pipe table wraps its cells at full text width only when one of its source
# lines is longer than pandoc's --columns (100). For tables too wide to fit
# unwrapped, stretch the separator past that limit, with dashes proportional
# to each column's longest cell, so pandoc gives wide columns more room.
def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def widen(table):
    sep = cells(table[1])
    rows = [cells(r) for r in table if r is not table[1]]
    longest = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(len(sep))]
    if sum(longest) + 3 * len(sep) <= 85:
        return table
    total = sum(longest)
    parts = []
    for s, n in zip(sep, longest):
        dashes = "-" * max(3, round(110 * n / total))
        parts.append((":" if s.startswith(":") else "") + dashes + (":" if s.endswith(":") else ""))
    return [table[0], "|" + "|".join(parts) + "|", *table[2:]]


final, block = [], []
for line in out + [""]:
    if line.startswith("|"):
        block.append(line)
        continue
    if block:
        final.extend(widen(block) if len(block) > 2 else block)
        block = []
    final.append(line)
Path("cuerpo.md").write_text("\n".join(final) + "\n", encoding="utf-8")
EOF

pandoc cuerpo.md \
  -f markdown-auto_identifiers+pipe_tables+raw_attribute+implicit_figures \
  -t latex --wrap=preserve --columns=100 --no-highlight --shift-heading-level-by=-1 \
  -o cuerpo.tex
rm cuerpo.md
tectonic --keep-logs informe.tex
echo "built $(pwd)/informe.pdf"
