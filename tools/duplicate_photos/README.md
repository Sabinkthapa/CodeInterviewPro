# Duplicate Photo Finder

`dedupe_photos.py` is a command-line Python tool that finds duplicate photos and deletes them, or moves them to another folder.

## Requirements

- Python 3.9 or newer
- [Pillow](https://pypi.org/project/Pillow/) is only needed for `--mode similar`, or for `--keep largest` (which reads image dimensions):
  ```bash
  pip install -r requirements.txt
  ```
- Optional HEIC/HEIF support in similar mode: `pip install pillow-heif`

## Detection modes

| Mode | Finds | How |
|------|-------|-----|
| `exact` (default) | Byte-identical copies | Groups files by size, then by the hash of the first 64 KiB, then by full SHA-256. Most files are never read in full. |
| `similar` | The same picture after resizing, re-compression or a format change | 64-bit perceptual difference hash (dHash). Images whose hashes differ by at most `--threshold` bits are grouped together. |

## Usage

```bash
# Dry run: list the duplicates without changing anything
python dedupe_photos.py ~/Pictures

# Delete exact duplicates (asks for confirmation)
python dedupe_photos.py ~/Pictures --delete

# Find visually similar photos, keep the highest resolution one, move the rest
python dedupe_photos.py ~/Pictures --mode similar --keep largest --move-to ~/Duplicates

# Scan several folders, write a CSV report, skip the confirmation prompt
python dedupe_photos.py ~/Pictures /mnt/backup/photos --report dupes.csv --delete -y
```

### Options

| Option | Description |
|--------|-------------|
| `--mode exact\|similar` | Detection mode (default `exact`) |
| `--threshold N` | Maximum number of differing bits in similar mode, 0–64 (default 5). Lower is stricter. |
| `--keep oldest\|newest\|shortest-path\|largest` | Which file in each group to keep (default `oldest`, by modification time) |
| `--delete` | Permanently delete duplicates |
| `--move-to DIR` | Move duplicates to `DIR` instead of deleting them. Name clashes get a `_1`, `_2` suffix. |
| `-y`, `--yes` | Skip the confirmation prompt |
| `--report FILE.csv` | Write every group (keep/remove, path, size) to a CSV file |
| `--no-recursive` | Don't scan sub-folders |
| `--extensions jpg,png` | Only scan these file extensions |

## Safety

- Nothing is changed unless you pass `--delete` or `--move-to`.
- One file from every duplicate group is always kept.
- Symlinks are ignored, and a file passed in twice (for example through overlapping folders) is counted only once.
- If you're unsure, run with `--move-to` first. Check the moved files, then delete the folder yourself.

## Running the tests

```bash
cd tools/duplicate_photos
pip install -r requirements-dev.txt
python -m pytest
```

The tests build their own sample images in temporary folders, so they never touch your real photos. GitHub Actions runs them on every pull request that changes this folder. The workflow tests Python 3.9–3.13 on Linux, plus Python 3.13 on Windows and macOS.
