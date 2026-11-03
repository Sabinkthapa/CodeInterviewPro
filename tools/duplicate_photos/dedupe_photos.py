#!/usr/bin/env python3
"""
dedupe_photos.py - find and remove duplicate photos.

Two detection modes:

  exact    (default) Byte-identical files. Files are grouped by size, then by
           a hash of the first 64 KiB, then by a full SHA-256, so most files
           are never read in full. Needs only the standard library.

  similar  Visually identical / near-identical images (resized, re-saved,
           re-compressed, different format). Uses a 64-bit difference hash
           (dHash) and groups images whose hashes differ by at most
           --threshold bits. Requires Pillow (pip install Pillow).

Safety:
  * Nothing is deleted unless you pass --delete. Without it the script only
    reports what it would do (dry run).
  * --delete asks for confirmation unless --yes is given.
  * --move-to DIR moves duplicates into DIR instead of deleting them.
  * One file in every group is always kept (see --keep).

Examples:
  python dedupe_photos.py ~/Pictures                     # report only
  python dedupe_photos.py ~/Pictures --delete            # delete exact dupes
  python dedupe_photos.py ~/Pictures --mode similar --threshold 5
  python dedupe_photos.py ~/Pictures --move-to ~/dupes --keep newest
  python dedupe_photos.py ~/Pictures --report dupes.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2",
}

CHUNK_SIZE = 1024 * 1024
PARTIAL_SIZE = 64 * 1024


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #

def iter_images(roots: Iterable[Path], recursive: bool,
                extensions: set[str]) -> Iterable[Path]:
    """Yield image files under the given roots, skipping symlinks."""
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates: Iterable[Path] = [root]
        elif recursive:
            candidates = root.rglob("*")
        else:
            candidates = root.glob("*")
        for path in candidates:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            if path.suffix.lower() not in extensions:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


# --------------------------------------------------------------------------- #
# Exact duplicate detection
# --------------------------------------------------------------------------- #

def _hash_file(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha256()
    remaining = limit
    with path.open("rb") as fh:
        while True:
            size = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
            if size == 0:
                break
            chunk = fh.read(size)
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return h.hexdigest()


def _regroup(groups: Iterable[list[Path]],
             key_fn: Callable[[Path], object]) -> list[list[Path]]:
    """Split each group by key_fn, keeping only sub-groups with 2+ members."""
    out: list[list[Path]] = []
    for group in groups:
        buckets: dict[object, list[Path]] = defaultdict(list)
        for path in group:
            try:
                buckets[key_fn(path)].append(path)
            except OSError as exc:
                print(f"  ! skipping {path}: {exc}", file=sys.stderr)
        out.extend(b for b in buckets.values() if len(b) > 1)
    return out


def find_exact_duplicates(files: list[Path]) -> list[list[Path]]:
    groups = _regroup([files], lambda p: p.stat().st_size)
    groups = _regroup(groups, lambda p: _hash_file(p, PARTIAL_SIZE))
    groups = _regroup(groups, _hash_file)
    return groups


# --------------------------------------------------------------------------- #
# Similar (perceptual) duplicate detection
# --------------------------------------------------------------------------- #

def _load_pillow():
    try:
        from PIL import Image, ImageOps  # noqa: F401
    except ImportError:
        sys.exit("--mode similar needs Pillow:  pip install Pillow")
    try:  # optional HEIC support
        from pillow_heif import register_heif_opener  # type: ignore
        register_heif_opener()
    except ImportError:
        pass
    return Image, ImageOps


def dhash(path: Path, hash_size: int = 8) -> int:
    """64-bit difference hash: compares adjacent pixels of a tiny greyscale copy."""
    Image, ImageOps = _load_pillow()
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # respect camera rotation
        img = img.convert("L").resize((hash_size + 1, hash_size),
                                      Image.Resampling.LANCZOS)
        pixels = img.tobytes()  # one byte per pixel in "L" mode
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def find_similar_duplicates(files: list[Path], threshold: int) -> list[list[Path]]:
    _load_pillow()
    hashed: list[tuple[Path, int]] = []
    for i, path in enumerate(files, 1):
        try:
            hashed.append((path, dhash(path)))
        except Exception as exc:  # unreadable / unsupported image
            print(f"  ! skipping {path}: {exc}", file=sys.stderr)
        if i % 200 == 0:
            print(f"  hashed {i}/{len(files)} images...", file=sys.stderr)

    uf = _UnionFind(len(hashed))
    if threshold == 0:
        by_hash: dict[int, int] = {}
        for i, (_, h) in enumerate(hashed):
            if h in by_hash:
                uf.union(by_hash[h], i)
            else:
                by_hash[h] = i
    else:
        # O(n^2) comparison on ints; fine for tens of thousands of photos.
        for i in range(len(hashed)):
            hi = hashed[i][1]
            for j in range(i + 1, len(hashed)):
                if bin(hi ^ hashed[j][1]).count("1") <= threshold:
                    uf.union(i, j)

    clusters: dict[int, list[Path]] = defaultdict(list)
    for i, (path, _) in enumerate(hashed):
        clusters[uf.find(i)].append(path)
    return [g for g in clusters.values() if len(g) > 1]


# --------------------------------------------------------------------------- #
# Choosing which file to keep
# --------------------------------------------------------------------------- #

def _resolution(path: Path) -> int:
    try:
        Image, _ = _load_pillow()
        with Image.open(path) as img:
            return img.width * img.height
    except Exception:
        return 0


KEEP_STRATEGIES: dict[str, Callable[[Path], object]] = {
    # sorted ascending; the first element is kept
    "oldest": lambda p: p.stat().st_mtime,
    "newest": lambda p: -p.stat().st_mtime,
    "shortest-path": lambda p: (len(str(p)), str(p)),
    "largest": lambda p: (-_resolution(p), -p.stat().st_size),
}


def split_keep(group: list[Path], strategy: str) -> tuple[Path, list[Path]]:
    ordered = sorted(group, key=lambda p: (KEEP_STRATEGIES[strategy](p), str(p)))
    return ordered[0], ordered[1:]


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #

def _unique_destination(dest_dir: Path, name: str) -> Path:
    target = dest_dir / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        target = dest_dir / f"{stem}_{n}{suffix}"
        n += 1
    return target


def remove_file(path: Path, move_to: Path | None) -> None:
    if move_to is None:
        path.unlink()
    else:
        move_to.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), _unique_destination(move_to, path.name))


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Find duplicate photos and optionally delete them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Without --delete or --move-to the script only reports (dry run).",
    )
    p.add_argument("paths", nargs="+", type=Path, help="folders or files to scan")
    p.add_argument("--mode", choices=("exact", "similar"), default="exact",
                   help="exact = identical bytes (default); "
                        "similar = visually alike (needs Pillow)")
    p.add_argument("--threshold", type=int, default=5,
                   help="max differing bits (0-64) for --mode similar (default 5)")
    p.add_argument("--keep", choices=sorted(KEEP_STRATEGIES), default="oldest",
                   help="which file of each group to keep (default: oldest)")
    p.add_argument("--no-recursive", action="store_true",
                   help="do not descend into sub-folders")
    p.add_argument("--extensions", default=None,
                   help="comma-separated extensions to include, e.g. jpg,png")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--delete", action="store_true",
                        help="permanently delete duplicates")
    action.add_argument("--move-to", type=Path, metavar="DIR",
                        help="move duplicates into DIR instead of deleting")
    p.add_argument("-y", "--yes", action="store_true",
                   help="do not ask for confirmation")
    p.add_argument("--report", type=Path, metavar="CSV",
                   help="write a CSV report of all duplicate groups")
    args = p.parse_args(argv)
    if not 0 <= args.threshold <= 64:
        p.error("--threshold must be between 0 and 64")
    for path in args.paths:
        if not path.exists():
            p.error(f"path does not exist: {path}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    extensions = IMAGE_EXTENSIONS
    if args.extensions:
        extensions = {"." + e.strip().lower().lstrip(".")
                      for e in args.extensions.split(",") if e.strip()}

    move_to = args.move_to.expanduser().resolve() if args.move_to else None
    roots = [p.expanduser() for p in args.paths]
    files = [f for f in iter_images(roots, not args.no_recursive, extensions)
             if move_to is None or move_to not in f.parents]
    print(f"Scanning {len(files)} image file(s) in mode '{args.mode}'...")

    if args.mode == "exact":
        groups = find_exact_duplicates(files)
    else:
        groups = find_similar_duplicates(files, args.threshold)

    if not groups:
        print("No duplicates found.")
        return 0

    plan: list[tuple[Path, list[Path]]] = []
    reclaim = 0
    for group in sorted(groups, key=lambda g: str(min(g))):
        keep, dupes = split_keep(group, args.keep)
        plan.append((keep, dupes))
        reclaim += sum(d.stat().st_size for d in dupes)

    total_dupes = sum(len(d) for _, d in plan)
    for n, (keep, dupes) in enumerate(plan, 1):
        print(f"\nGroup {n}:")
        print(f"  KEEP    {keep}")
        for d in dupes:
            print(f"  REMOVE  {d}")
    print(f"\n{len(plan)} group(s), {total_dupes} duplicate file(s), "
          f"{human_size(reclaim)} reclaimable.")

    if args.report:
        with args.report.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["group", "action", "path", "size_bytes"])
            for n, (keep, dupes) in enumerate(plan, 1):
                writer.writerow([n, "keep", keep, keep.stat().st_size])
                for d in dupes:
                    writer.writerow([n, "remove", d, d.stat().st_size])
        print(f"Report written to {args.report}")

    if not (args.delete or move_to):
        print("\nDry run: nothing was changed. "
              "Use --delete or --move-to DIR to act on these files.")
        return 0

    verb = f"move {total_dupes} file(s) to {move_to}" if move_to \
        else f"PERMANENTLY DELETE {total_dupes} file(s)"
    if not args.yes:
        answer = input(f"\nAbout to {verb}. Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    done = failed = 0
    for _, dupes in plan:
        for d in dupes:
            try:
                remove_file(d, move_to)
                done += 1
            except OSError as exc:
                failed += 1
                print(f"  ! could not remove {d}: {exc}", file=sys.stderr)
    action_word = "Moved" if move_to else "Deleted"
    print(f"{action_word} {done} file(s)" + (f", {failed} failed." if failed else "."))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
