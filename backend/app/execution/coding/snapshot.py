"""Validate GitHub snapshots before transferring any repository-controlled files."""

from __future__ import annotations

import base64
import io
import tarfile

from app.execution.providers.native.github.safety import SECRET, safe_path


def validated_snapshot(content: bytes, *, include_binary=False) -> dict:
    files = {}
    size = 0
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
        for entry in archive:
            if len(files) >= 10000:
                raise ValueError("Repository snapshot exceeds the file budget.")
            relative = entry.name.partition("/")[2]
            if not relative or entry.isdir():
                continue
            safe_path(relative)
            if not entry.isfile() or entry.issym() or entry.islnk():
                raise ValueError("Snapshot symlinks and special files are not supported.")
            size += entry.size
            if entry.size > 2_000_000 or size > 40_000_000:
                raise ValueError("Repository snapshot exceeds the source size budget.")
            reader = archive.extractfile(entry)
            if reader is None:
                raise ValueError("Repository snapshot has unreadable file content.")
            raw = reader.read(entry.size + 1)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                if include_binary:
                    files[relative] = {"binaryBase64": base64.b64encode(raw).decode()}
                continue
            if SECRET.search(text):
                raise ValueError(
                    "Repository snapshot contains a detected credential; remove it before coding."
                )
            if relative in files:
                raise ValueError("Repository snapshot contains duplicate paths.")
            files[relative] = text
    return files
