from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PRODUCT_NAME = "面型及Rxy分析工具"
PRODUCT_ID = "Surface Rxy ZXY Analyzer"
EXECUTABLE_NAME = "SurfaceRxyZxyAnalyzer.exe"


def version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Expected semantic version x.y.z, got {version!r}")
    return int(parts[0]), int(parts[1]), int(parts[2]), 0


def write_version_info(path: Path, version: str) -> None:
    numeric = version_tuple(version)
    comma_version = ", ".join(str(item) for item in numeric)
    dotted_version = ".".join(str(item) for item in numeric)
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({comma_version}),
    prodvers=({comma_version}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404B0',
        [
          StringStruct(u'CompanyName', u'GCT'),
          StringStruct(u'FileDescription', u'{PRODUCT_NAME}'),
          StringStruct(u'FileVersion', u'{dotted_version}'),
          StringStruct(u'InternalName', u'SurfaceRxyZxyAnalyzer'),
          StringStruct(u'OriginalFilename', u'{EXECUTABLE_NAME}'),
          StringStruct(u'ProductName', u'{PRODUCT_NAME}'),
          StringStruct(u'ProductVersion', u'{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_build_info(path: Path, version: str) -> None:
    payload = {
        "version": version,
        "source_commit": git_commit(),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        ).stdout.strip()
        return f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_manifest(path: Path, artifact: Path, version: str) -> None:
    checksum = sha256(artifact)
    payload = {
        "schema_version": 1,
        "product": PRODUCT_ID,
        "channel": "stable",
        "version": version,
        "artifact": artifact.name,
        "artifact_type": "full_installer",
        "sha256": checksum,
        "size_bytes": artifact.stat().st_size,
        "minimum_upgrade_version": "4.0.2",
        "source_commit": git_commit(),
        "build_platform": platform.platform(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifact.with_suffix(artifact.suffix + ".sha256").write_text(
        f"{checksum}  {artifact.name}\n",
        encoding="ascii",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--version-file", type=Path)
    parser.add_argument("--build-info", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    version_tuple(args.version)
    if args.version_file:
        write_version_info(args.version_file, args.version)
    if args.build_info:
        write_build_info(args.build_info, args.version)
    if args.artifact or args.manifest:
        if not args.artifact or not args.manifest:
            parser.error("--artifact and --manifest must be supplied together")
        write_manifest(args.manifest, args.artifact, args.version)


if __name__ == "__main__":
    main()
