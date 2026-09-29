"""Read Photoshop ATN v16 descriptors without executing the actions."""
from __future__ import annotations
import argparse
import io
import json
import struct
from pathlib import Path
from psd_tools.psd.descriptor import Descriptor


def inspect_action(path: Path) -> dict:
    source = path.read_bytes()
    stream = io.BytesIO(source)

    def number():
        return struct.unpack(">I", stream.read(4))[0]

    def unicode_string():
        return stream.read(number() * 2).decode("utf-16be").rstrip("\0")

    def string():
        return stream.read(number()).decode("utf-8", "replace")

    def plain(value):
        if isinstance(value, dict) or isinstance(value, Descriptor):
            return {key.decode("ascii", "replace") if isinstance(key, bytes) else str(key): plain(item)
                    for key, item in value.items()}
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, bytes):
            return value.decode("ascii", "replace")
        if hasattr(value, "enum"):
            return {"type": plain(value.typeID), "value": plain(value.enum)}
        if hasattr(value, "value"):
            return plain(value.value)
        if hasattr(value, "__iter__"):
            return [plain(item) for item in value]
        return {"kind": type(value).__name__}

    version = number()
    if version != 16:
        raise ValueError(f"Unsupported ATN version: {version}")
    result = dict(version=version, name=unicode_string())
    stream.read(1)
    actions = []
    for _ in range(number()):
        stream.read(6)  # shortcut, modifiers, palette colour
        action = dict(name=unicode_string())
        stream.read(1)
        steps = []
        for _ in range(number()):
            flags = stream.read(4)
            event_type = stream.read(4)
            if event_type not in (b"TEXT", b"long"):
                raise ValueError(f"Unsupported event type: {event_type!r}")
            event = string() if event_type == b"TEXT" else stream.read(4).decode("ascii")
            name = string()
            descriptor_flag = number()
            if descriptor_flag not in (0, 0xFFFFFFFF):
                raise ValueError("Invalid descriptor marker")
            descriptor = Descriptor.read(stream) if descriptor_flag else None
            steps.append(dict(event=event, label=name, flags=flags.hex(), parameters=plain(descriptor)))
        action["steps"] = steps
        actions.append(action)
    result["actions"] = actions
    result["bytes_read"] = stream.tell()
    if stream.tell() != len(source):
        raise ValueError("Unparsed trailing data")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = json.dumps(inspect_action(args.path), indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
