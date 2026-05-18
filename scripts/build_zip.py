"""
Build joe-property-analysis-v1.1.zip from the allow-list.

Stages files into output/dist/staging/joe-property-analysis-v1.1/ then
zips to output/dist/joe-property-analysis-v1.1.zip.

Run: python -m scripts.build_zip
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING_ROOT = ROOT / "output" / "dist" / "staging"
PACKAGE_NAME = "joe-property-analysis-v1.1"
STAGING = STAGING_ROOT / PACKAGE_NAME
ZIP_OUT = ROOT / "output" / "dist" / f"{PACKAGE_NAME}.zip"

IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".venv", "venv",
    ".env", "*.env.local", ".DS_Store", "Thumbs.db",
    "rentcast_cache", ".rentcast_cache",
)

# Per-machine state Joe should NOT inherit
DATA_SKIP = {"deal_intake.db"}

# Inside output/projects/Joe Berlin/ keep only these two files
JOE_FOLDER_KEEP = {"reference_data.xlsx", "README.md"}


def fresh_staging():
    if STAGING_ROOT.exists():
        shutil.rmtree(STAGING_ROOT)
    STAGING.mkdir(parents=True)


def copy_tree(rel: str, skip_files: set[str] | None = None):
    src = ROOT / rel
    dst = STAGING / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if skip_files is None:
        shutil.copytree(src, dst, ignore=IGNORE_PATTERNS)
    else:
        def ignore_with_skip(directory, contents):
            base = set(IGNORE_PATTERNS(directory, contents))
            for f in contents:
                if f in skip_files:
                    base.add(f)
            return list(base)
        shutil.copytree(src, dst, ignore=ignore_with_skip)


def copy_file(rel: str):
    src = ROOT / rel
    if not src.exists():
        return
    dst = STAGING / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_joe_folder_selective():
    src_folder = ROOT / "output" / "projects" / "Joe Berlin"
    dst_folder = STAGING / "output" / "projects" / "Joe Berlin"
    dst_folder.mkdir(parents=True)
    for name in JOE_FOLDER_KEEP:
        src_file = src_folder / name
        if src_file.exists():
            shutil.copy2(src_file, dst_folder / name)


def main():
    fresh_staging()
    print(f"Staging at: {STAGING}")

    # Skill
    copy_file(".claude/commands/property-analysis.md")

    # Code
    copy_tree("scripts/property_analysis")
    copy_tree("webapp")

    # Bundled data + templates (minus deal_intake.db so Joe starts blank)
    copy_tree("templates/property_analysis", skip_files=DATA_SKIP)

    # Joe folder — only reference_data.xlsx + README.md
    copy_joe_folder_selective()

    # Top-level docs + manifest
    for f in [
        "requirements.txt",
        "INSTALL.md",
        "JOE_USER_MANUAL.md",
        "AGENTIC_DELIVERY.md",
        "AGENT_STATE.md",
        "AGENT_BACKLOG.md",
    ]:
        copy_file(f)

    # Verification
    leakage = []
    for path in STAGING.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        rel = path.relative_to(STAGING)
        if "__pycache__" in path.parts:
            leakage.append(("__pycache__", rel))
        if name in {".env", "Thumbs.db", ".DS_Store"} or name.endswith(".pyc"):
            leakage.append(("cache/env", rel))
        if name == "deal_intake.db":
            leakage.append(("deal_intake.db leaked", rel))
    if leakage:
        print("LEAKAGE FOUND:")
        for label, p in leakage:
            print(f"  [{label}] {p}")
        raise SystemExit(1)
    print("Verification: no leakage")

    file_count = sum(1 for p in STAGING.rglob("*") if p.is_file())
    total_size = sum(p.stat().st_size for p in STAGING.rglob("*") if p.is_file())
    print(f"  {file_count} files / {total_size/1024/1024:.1f} MB uncompressed")

    ZIP_OUT.parent.mkdir(parents=True, exist_ok=True)
    if ZIP_OUT.exists():
        ZIP_OUT.unlink()
    print(f"\nZipping to {ZIP_OUT}")
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(STAGING.rglob("*")):
            if path.is_file():
                arcname = path.relative_to(STAGING_ROOT)
                zf.write(path, arcname)

    print(f"  zip size: {ZIP_OUT.stat().st_size/1024/1024:.1f} MB")
    with zipfile.ZipFile(ZIP_OUT) as zf:
        entries = zf.namelist()
    print(f"  {len(entries)} entries inside the zip")
    top = sorted({n.split('/')[1] for n in entries if '/' in n})
    print(f"  top-level inside: {top}")

    # Clean up the staging dir now that we have the zip
    shutil.rmtree(STAGING_ROOT)
    print("\nDONE")


if __name__ == "__main__":
    main()
