"""Read/write terraform.tfvars (HCL primitives only — strings, numbers, bools, lists)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(path: Path) -> dict[str, Any]:
    """Parse a tfvars file, returning a dict of name → value.

    Comment lines (#, //, or commented-out variable lines starting with `# foo =`)
    are skipped. Multiline values aren't supported — keep the file flat.
    """
    out: dict[str, Any] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).rstrip()
        out[name] = _decode_value(value)
    return out


def _decode_value(s: str) -> Any:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s in ("true", "false"):
        return s == "true"
    if s.startswith("[") and s.endswith("]"):
        # Comma-separated string list.
        inner = s[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip().strip('"') for p in inner.split(",")]
        return parts
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def render(values: dict[str, Any]) -> str:
    """Render a dict of values back to tfvars text. Stable key order."""
    lines: list[str] = []
    for k in sorted(values):
        lines.append(f"{k} = {_encode_value(values[k])}")
    return "\n".join(lines) + "\n"


def _encode_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_encode_value(x) for x in v) + "]"
    return f'"{v}"'


def write(path: Path, values: dict[str, Any]) -> None:
    path.write_text(render(values))
