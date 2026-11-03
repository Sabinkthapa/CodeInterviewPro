# Duplicate Photo Finder

`dedupe_photos.py` is a command-line Python tool that finds duplicate photos and sends them to the Recycle Bin/Trash, deletes them permanently, or moves them to another folder.

## Requirements

- Python 3.10 or newer
- Optional libraries, installed together with `pip install -r requirements.txt`:
  - [Pillow](https://pypi.org/project/Pillow/), needed for `--mode similar` and `--keep largest` (which reads image dimensions).
  - [send2trash](https://pypi.org/project/Send2Trash/), needed for `--trash`.
- `--review` also needs Tkinter, which comes with Python on Windows and with the python.org installers for macOS. Elsewhere you may need to install it: `sudo apt install python3-tk` on Debian/Ubuntu, or `brew install python-tk` with Homebrew.
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

# Send exact duplicates to the Recycle Bin/Trash (asks for confirmation)
python dedupe_photos.py ~/Pictures --trash

# Look at every group of similar photos first and choose what to keep,
# then send the rest to the Trash
python dedupe_photos.py ~/Pictures --mode similar --review --trash

# Delete exact duplicates permanently
python dedupe_photos.py ~/Pictures --delete

# Find visually similar photos, keep the highest resolution one, move the rest
python dedupe_photos.py ~/Pictures --mode similar --keep largest --move-to ~/Duplicates

# Scan several folders, write a CSV report, skip the confirmation prompt
python dedupe_photos.py ~/Pictures /mnt/backup/photos --report dupes.csv --trash -y
```

### Options

| Option | Description |
|--------|-------------|
| `--mode exact\|similar` | Detection mode (default `exact`) |
| `--threshold N` | Maximum number of differing bits in similar mode, 0–64 (default 5). Lower is stricter. |
| `--keep oldest\|newest\|shortest-path\|largest` | Which file in each group to keep (default `oldest`, by modification time) |
| `--trash` | Send duplicates to the Recycle Bin (Windows) or Trash (macOS, Linux), so they can be restored |
| `--delete` | Permanently delete duplicates |
| `--move-to DIR` | Move duplicates to `DIR` instead of deleting them. Name clashes get a `_1`, `_2` suffix. |
| `--review` | Open a window to choose which photos to keep before the action runs. Needs `--trash`, `--delete` or `--move-to`. |
| `-y`, `--yes` | Skip the confirmation prompt (and the review window's confirmation dialog) |
| `--report FILE.csv` | Write every group (keep/remove, path, size) to a CSV file |
| `--no-recursive` | Don't scan sub-folders |
| `--extensions jpg,png` | Only scan these file extensions |

## Reviewing duplicates in a window

`--review` opens a window before anything is removed. It shows one group of duplicates at a time, with a thumbnail of each photo and its file name, folder, resolution, size and date.

- The photo picked by `--keep` starts out marked **KEEP** (green border). The rest are marked **REMOVE** (red border).
- Click a photo, tick its **Keep** box, or press its number (1–9) to change it. You can keep more than one photo in a group, but not zero.
- **Keep only this** keeps just that photo. **Keep all in group** and **Reset group** apply to the group on screen.
- **Previous** and **Next** (or the Left and Right arrow keys) move between groups, and your choices are remembered.
- The button at the bottom right runs the action you chose on the command line, for example **Send to Trash: 12 file(s)**. It asks you to confirm first. **Cancel**, Esc or closing the window changes nothing.

## Safety

- Nothing is changed unless you pass `--trash`, `--delete` or `--move-to`. You can use only one of them at a time.
- `--trash` is the safest way to remove duplicates. You can restore a photo from the Recycle Bin/Trash, and it goes back to its original folder. On Linux, files go to the standard desktop Trash (`~/.local/share/Trash`).
- Every action asks for confirmation unless you pass `-y`.
- One file from every duplicate group is always kept.
- Symlinks are ignored, and a file passed in twice (for example through overlapping folders) is counted only once.
- Files in the Trash still use disk space until you empty it. Use `--delete` when you're sure and need the space straight away.

## Running the tests

```bash
cd tools/duplicate_photos
pip install -r requirements-dev.txt
python -m pytest
```

The tests build their own sample images in temporary folders, so they never touch your real photos or your real Trash. GitHub Actions runs them on every pull request that changes this folder. The workflow tests Python 3.10–3.13 on Linux, plus Python 3.13 on Windows and macOS.
