"""Build tags.yaml from an I/O list CSV:  python gen_tags.py io_list.csv [tags.yaml]

CSV columns (header row required, Korean headers also accepted):
  name, address, type, direction, scale, comment
Blank cells are filled in: name = address, type from the device (X/Y/M bit, D word, D with a scale float),
direction X = to_plc and Y = from_plc. M and D need a direction. A row with only a first cell starting with #
is a comment. Everything above the "tags:" line of an existing output file (plc, opcua, heartbeat...) is kept.
"""
import csv
import io
import re
import shutil
import sys
from pathlib import Path

import yaml

from bridge import validate

COLS = {
    "name": "name", "이름": "name", "태그": "name", "태그명": "name",
    "address": "address", "주소": "address", "디바이스": "address",
    "type": "type", "타입": "type", "형식": "type",
    "direction": "direction", "방향": "direction",
    "scale": "scale", "배율": "scale", "스케일": "scale",
    "comment": "comment", "설명": "comment", "코멘트": "comment", "비고": "comment",
}
DIRS = {"to_plc": "to_plc", "in": "to_plc", "입력": "to_plc", "from_plc": "from_plc", "out": "from_plc", "출력": "from_plc"}
HEADER = """plc:
  driver: mx            # mx | mock
  station: 1            # MX logical station number (Communication Setup Utility)
opcua:
  endpoint: opc.tcp://127.0.0.1:4840
  namespace: urn:factory-bridge
cycle_ms: 30
"""
FIO_PREFIX, FIO_LIMIT = "FIO_", 256  # Factory I/O browses at most 256 nodes; filter "Contain" = FIO_


def read_rows(path):
    """[(csv line number, {column: cell})] for the data rows."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")  # Excel on Korean Windows saves plain "CSV" as cp949
    reader = csv.reader(io.StringIO(text, newline=""))
    head, rows = None, []
    for r in reader:
        cells = [c.strip() for c in r]
        if not any(cells) or (cells[0].startswith("#") and not any(cells[1:])):
            continue  # blank line or comment line
        if head is None:
            head = [COLS.get(c.lower()) for c in cells]
            if "address" not in head:
                raise ValueError(f"header row needs an address column, got {r}")
            twice = sorted({h for h in head if h and head.count(h) > 1})
            if twice:
                raise ValueError(f"header row has more than one column for: {', '.join(twice)}")
            continue
        rows.append((reader.line_num, {k: c for k, c in zip(head, cells) if k}))
    if head is None:
        raise ValueError("the CSV is empty")
    return rows


def to_tag(row, line):
    addr = row.get("address", "").upper()
    if not addr:
        raise ValueError(f"line {line}: address is empty")
    scale = row.get("scale", "")
    typ = row.get("type", "").lower() or ("bit" if addr[0] != "D" else "float" if scale else "word")
    direction = row.get("direction", "").lower() or {"X": "to_plc", "Y": "from_plc"}.get(addr[0], "")
    if direction not in DIRS:
        raise ValueError(f"line {line} ({addr}): direction must be to_plc or from_plc, got {direction!r}")
    tag = {"name": row.get("name") or addr, "address": addr, "type": typ, "direction": DIRS[direction]}
    if scale:
        try:
            tag["scale"] = int(scale) if scale.isdigit() else float(scale)
        except ValueError:
            raise ValueError(f"line {line} ({addr}): scale must be a number, got {scale!r}") from None
    comment = " ".join("".join(ch if ch.isprintable() else " " for ch in row.get("comment", "")).split())
    return tag, comment


def render(header, tags):
    out = [header.rstrip("\n"), "tags:"] if header.strip() else ["tags:"]
    for tag, comment in tags:
        # safe_dump quotes what YAML would misread (a name like ON, a scale like 1e-05)
        body = yaml.safe_dump(tag, default_flow_style=True, sort_keys=False, allow_unicode=True, width=10**6).strip()
        out.append(f"  - {body}" + (f"   # {comment}" if comment else ""))
    return "\n".join(out) + "\n"


def keep_header(old):
    """The part of an existing tags.yaml above 'tags:'. Refuses to drop settings written below the tag list."""
    m = re.search(r"(?m)^tags:", old)
    header = old[:m.start()] if m else old
    lost = set(yaml.safe_load(old) or {}) - {"tags"} - set(yaml.safe_load(header) or {})
    if lost:
        raise ValueError(f"move {', '.join(sorted(lost))} above the 'tags:' line of the existing file, or it would be lost")
    return header


def main(src, dst="tags.yaml"):
    tags = [to_tag(r, line) for line, r in read_rows(src)]
    dst = Path(dst)
    try:
        header = keep_header(dst.read_text(encoding="utf-8-sig")) if dst.exists() else HEADER
        text = render(header, tags)
        validate(yaml.safe_load(text))  # same checks the bridge runs at start; nothing is written if they fail
    except yaml.YAMLError as e:
        raise ValueError(f"not valid YAML: {e}") from None
    if dst.exists():
        shutil.copyfile(dst, dst.with_name(dst.name + ".bak"))
    dst.write_text(text, encoding="utf-8")
    names = [t["name"] for t, _ in tags]
    fio = sum(n.startswith(FIO_PREFIX) for n in names)
    n_to = sum(t["direction"] == "to_plc" for t, _ in tags)
    print(f"{dst}: {len(tags)} tags ({n_to} to_plc, {len(tags) - n_to} from_plc), {fio} named {FIO_PREFIX}*")
    if (fio or len(tags)) > FIO_LIMIT:
        print(f"[!] more than {FIO_LIMIT} tags for Factory I/O: it browses at most {FIO_LIMIT} nodes")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    try:
        main(*sys.argv[1:])
    except (ValueError, OSError) as e:
        sys.exit(f"error: {e}")
