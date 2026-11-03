"""
Visual review window for dedupe_photos.py (used by the --review option).

Shows each group of duplicates as thumbnails, side by side, with the file's
folder, resolution, size and date. Tick "Keep" on the photos to keep; every
photo left unticked is removed with the action chosen on the command line
(--trash, --delete or --move-to) when you click the action button.

The window is built with Tkinter, which ships with Python. Thumbnails need
Pillow. The selection logic lives in ReviewState, which has no GUI code, so
it can be tested without a display.

Keyboard: Left/Right switch groups, 1-9 toggle "Keep" on a photo,
Esc cancels.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Callable, Sequence

Plan = list[tuple[list[Path], list[Path]]]  # per group: (keep, remove)


class ReviewUnavailable(RuntimeError):
    """The review window can't be shown (no Tkinter or no display)."""


# --------------------------------------------------------------------------- #
# Selection state (no GUI code)
# --------------------------------------------------------------------------- #

def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class ReviewState:
    """Which photos in each duplicate group are kept.

    Every group always keeps at least one photo: un-keeping the last kept
    photo of a group is refused.
    """

    def __init__(self, plan: Sequence[tuple[Sequence[Path], Sequence[Path]]]):
        self.files: list[list[Path]] = []
        self.keep: list[list[bool]] = []
        self._default: list[list[bool]] = []
        for keeps, removes in plan:
            if not keeps:
                raise ValueError("every group must keep at least one file")
            files = list(keeps) + list(removes)
            flags = [True] * len(keeps) + [False] * len(removes)
            self.files.append(files)
            self.keep.append(flags)
            self._default.append(list(flags))
        self._sizes = {p: _file_size(p) for group in self.files for p in group}

    def __len__(self) -> int:
        return len(self.files)

    def size(self, path: Path) -> int:
        return self._sizes.get(path, 0)

    def is_kept(self, group: int, index: int) -> bool:
        return self.keep[group][index]

    def set_keep(self, group: int, index: int, value: bool) -> bool:
        """Set one photo's keep flag. Returns False if that was refused."""
        flags = self.keep[group]
        if not value and flags[index] and sum(flags) == 1:
            return False
        flags[index] = value
        return True

    def toggle(self, group: int, index: int) -> bool:
        return self.set_keep(group, index, not self.keep[group][index])

    def keep_only(self, group: int, index: int) -> None:
        self.keep[group] = [i == index for i in range(len(self.files[group]))]

    def keep_all(self, group: int) -> None:
        self.keep[group] = [True] * len(self.files[group])

    def reset(self, group: int) -> None:
        self.keep[group] = list(self._default[group])

    def removals(self, group: int) -> list[Path]:
        return [p for p, k in zip(self.files[group], self.keep[group]) if not k]

    def group_remove_bytes(self, group: int) -> int:
        return sum(self.size(p) for p in self.removals(group))

    def total_remove_count(self) -> int:
        return sum(len(self.removals(g)) for g in range(len(self)))

    def total_remove_bytes(self) -> int:
        return sum(self.group_remove_bytes(g) for g in range(len(self)))

    def result(self) -> Plan:
        out: Plan = []
        for files, flags in zip(self.files, self.keep):
            out.append(([p for p, k in zip(files, flags) if k],
                        [p for p, k in zip(files, flags) if not k]))
        return out


def common_folder(paths: Sequence[Path]) -> Path | None:
    """The deepest folder containing every path, or None (e.g. other drives)."""
    try:
        return Path(os.path.commonpath([str(p.parent) for p in paths]))
    except ValueError:
        return None


def short_folder(path: Path, base: Path | None) -> str:
    """The file's folder relative to base, e.g. "2023/holiday" or "."."""
    if base is None:
        return str(path.parent)
    try:
        rel = path.parent.relative_to(base)
    except ValueError:
        return str(path.parent)
    return "(top folder)" if str(rel) == "." else rel.as_posix()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# --------------------------------------------------------------------------- #
# Thumbnails
# --------------------------------------------------------------------------- #

def load_thumbnail(path: Path, max_size: int):
    """Return (PIL image or None, (width, height) or None)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, None
    try:  # optional HEIC support, as in dedupe_photos
        from pillow_heif import register_heif_opener  # type: ignore
        register_heif_opener()
    except ImportError:
        pass
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            dims = img.size
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            return img, dims
    except Exception:
        return None, None


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #

KEEP_COLOR = "#1e8e3e"
REMOVE_COLOR = "#c5221f"


class ReviewWindow:
    """The review window. Call run_review() rather than using this directly."""

    def __init__(self, root, state: ReviewState, action_verb: str,
                 confirm: bool = True, ask: Callable[[str, str], bool] | None = None,
                 thumb_size: int = 220, columns: int = 4):
        import tkinter as tk
        from tkinter import font, messagebox, ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        self.state = state
        self.action_verb = action_verb
        self.confirm = confirm
        self.ask = ask or messagebox.askyesno
        self.thumb_size = thumb_size
        self.columns = columns
        self.group = 0
        self.result: Plan | None = None
        self.cards: list[dict] = []
        self._thumbs: dict[Path, object] = {}   # PhotoImage cache
        self._dims: dict[Path, tuple[int, int] | None] = {}
        self.base = common_folder([p for group in state.files for p in group])

        base_font = font.nametofont("TkDefaultFont")
        self.bold_font = base_font.copy()
        self.bold_font.configure(weight="bold")
        self.title_font = base_font.copy()
        self.title_font.configure(weight="bold",
                                  size=abs(base_font.cget("size")) + 4)

        root.title("Review duplicate photos")
        root.geometry("1120x760")
        root.minsize(720, 480)
        root.protocol("WM_DELETE_WINDOW", self.cancel)

        # Header
        top = ttk.Frame(root, padding=(12, 10))
        top.pack(fill="x")
        self.title_label = ttk.Label(top, font=self.title_font)
        self.title_label.pack(side="left")
        ttk.Button(top, text="Reset group", command=self.reset_group).pack(side="right")
        ttk.Button(top, text="Keep all in group",
                   command=self.keep_all).pack(side="right", padx=6)
        if self.base is not None:
            ttk.Label(root, text=f"Folder: {self.base}", padding=(12, 0),
                      foreground="#5f6368").pack(fill="x")
        self.group_label = ttk.Label(root, padding=(12, 4))
        self.group_label.pack(fill="x")
        self.message = ttk.Label(root, padding=(12, 2), foreground=REMOVE_COLOR)
        self.message.pack(fill="x")

        # Scrollable card area
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, padx=8)
        self.canvas = tk.Canvas(body, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        for seq, delta in (("<MouseWheel>", None), ("<Button-4>", -1),
                           ("<Button-5>", 1)):
            self.canvas.bind_all(seq, lambda e, d=delta: self._scroll(e, d))

        # Footer
        bottom = ttk.Frame(root, padding=(12, 10))
        bottom.pack(fill="x")
        self.prev_button = ttk.Button(bottom, text="Previous", command=self.prev_group)
        self.prev_button.pack(side="left")
        self.next_button = ttk.Button(bottom, text="Next", command=self.next_group)
        self.next_button.pack(side="left", padx=6)
        self.total_label = ttk.Label(bottom)
        self.total_label.pack(side="left", padx=12)
        self.apply_button = ttk.Button(bottom, command=self.apply)
        self.apply_button.pack(side="right")
        ttk.Button(bottom, text="Cancel", command=self.cancel).pack(side="right", padx=6)

        root.bind("<Left>", lambda e: self.prev_group())
        root.bind("<Right>", lambda e: self.next_group())
        root.bind("<Escape>", lambda e: self.cancel())
        for n in range(1, 10):
            root.bind(str(n), lambda e, i=n - 1: self.toggle(i))

        self.show_group(0)

    # ----------------------------------------------------------------- view

    def _scroll(self, event, delta):
        if delta is None:  # Windows / macOS wheel
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def _thumbnail(self, path: Path):
        if path not in self._thumbs:
            img, dims = load_thumbnail(path, self.thumb_size)
            photo = None
            if img is not None:
                try:
                    from PIL import ImageTk
                    photo = ImageTk.PhotoImage(img, master=self.root)
                except Exception:
                    photo = None
            self._thumbs[path] = photo
            self._dims[path] = dims
        return self._thumbs[path]

    def show_group(self, index: int) -> None:
        self.group = max(0, min(index, len(self.state) - 1))
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.cards = []
        tk, ttk = self.tk, self.ttk

        for i, path in enumerate(self.state.files[self.group]):
            card = tk.Frame(self.grid_frame, bd=0, highlightthickness=3, padx=6, pady=6)
            card.grid(row=i // self.columns, column=i % self.columns,
                      padx=6, pady=6, sticky="n")
            photo = self._thumbnail(path)
            if photo is not None:
                thumb = tk.Label(card, image=photo, cursor="hand2")
            else:
                thumb = tk.Label(card, text="No preview", width=26, height=10,
                                 relief="groove", cursor="hand2")
            thumb.pack()
            thumb.bind("<Button-1>", lambda e, i=i: self.toggle(i))
            thumb.bind("<Double-Button-1>", lambda e, i=i: self.keep_only(i))

            var = tk.BooleanVar(value=self.state.is_kept(self.group, i))
            check = ttk.Checkbutton(card, text=f"Keep  [{i + 1}]" if i < 9 else "Keep",
                                    variable=var, command=lambda i=i: self._on_check(i))
            check.pack(anchor="w", pady=(6, 0))
            status = tk.Label(card, font=self.bold_font)
            status.pack(anchor="w")

            dims = self._dims.get(path)
            modified = ""
            try:
                modified = _dt.datetime.fromtimestamp(path.stat().st_mtime) \
                    .strftime("%Y-%m-%d %H:%M")
            except OSError:
                pass
            details = " · ".join(filter(None, [
                f"{dims[0]}×{dims[1]}" if dims else "",
                human_size(self.state.size(path)),
            ]))
            ttk.Label(card, text=path.name, font=self.bold_font,
                      wraplength=self.thumb_size).pack(anchor="w")
            ttk.Label(card, text=short_folder(path, self.base),
                      wraplength=self.thumb_size,
                      foreground="#5f6368").pack(anchor="w")
            ttk.Label(card, text=details).pack(anchor="w")
            if modified:
                ttk.Label(card, text=f"Modified {modified}").pack(anchor="w")
            ttk.Button(card, text="Keep only this",
                       command=lambda i=i: self.keep_only(i)).pack(anchor="w", pady=(4, 0))

            self.cards.append({"frame": card, "var": var, "status": status,
                               "path": path})
        self.canvas.yview_moveto(0)
        self.message.configure(text="")
        self.refresh()

    def refresh(self) -> None:
        """Update every label and colour to match the current state."""
        g, n = self.group, len(self.state)
        files = self.state.files[g]
        self.title_label.configure(text=f"Group {g + 1} of {n}  ·  {len(files)} photos")
        removing = self.state.removals(g)
        self.group_label.configure(
            text=f"Keeping {len(files) - len(removing)}, removing {len(removing)} "
                 f"({human_size(self.state.group_remove_bytes(g))}). "
                 "Click a photo or press its number to toggle Keep.")
        for i, card in enumerate(self.cards):
            kept = self.state.is_kept(g, i)
            card["var"].set(kept)
            color = KEEP_COLOR if kept else REMOVE_COLOR
            card["frame"].configure(highlightbackground=color, highlightcolor=color)
            card["status"].configure(text="KEEP" if kept else "REMOVE", fg=color)
        total = self.state.total_remove_count()
        self.total_label.configure(
            text=f"Total: {total} file(s) to remove, "
                 f"{human_size(self.state.total_remove_bytes())} to free")
        self.apply_button.configure(text=f"{self.action_verb}: {total} file(s)")
        self.prev_button.state(["!disabled"] if g > 0 else ["disabled"])
        self.next_button.state(["!disabled"] if g < n - 1 else ["disabled"])

    # -------------------------------------------------------------- actions

    def _on_check(self, index: int) -> None:
        wanted = self.cards[index]["var"].get()
        self._set(index, wanted)

    def _set(self, index: int, value: bool) -> None:
        if self.state.set_keep(self.group, index, value):
            self.message.configure(text="")
        else:
            self.message.configure(text="Each group must keep at least one photo.")
        self.refresh()

    def toggle(self, index: int) -> None:
        if 0 <= index < len(self.cards):
            self._set(index, not self.state.is_kept(self.group, index))

    def keep_only(self, index: int) -> None:
        self.state.keep_only(self.group, index)
        self.message.configure(text="")
        self.refresh()

    def keep_all(self) -> None:
        self.state.keep_all(self.group)
        self.refresh()

    def reset_group(self) -> None:
        self.state.reset(self.group)
        self.refresh()

    def next_group(self) -> None:
        if self.group < len(self.state) - 1:
            self.show_group(self.group + 1)

    def prev_group(self) -> None:
        if self.group > 0:
            self.show_group(self.group - 1)

    def apply(self) -> None:
        total = self.state.total_remove_count()
        if total and self.confirm:
            question = (f"{self.action_verb}: {total} file(s), "
                        f"{human_size(self.state.total_remove_bytes())}?\n\n"
                        "Photos marked KEEP are not touched.")
            if not self.ask("Confirm", question):
                return
        self.result = self.state.result()
        self._close()

    def cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")
        finally:
            self.root.quit()


def run_review(plan: Plan, action_verb: str, confirm: bool = True) -> Plan | None:
    """Open the review window and block until it closes.

    Returns the reviewed plan, or None if the user cancelled.
    Raises ReviewUnavailable if no window can be shown.
    """
    if not plan:
        return []
    try:
        import tkinter as tk
    except ImportError as exc:
        raise ReviewUnavailable(
            "--review needs Tkinter, which this Python was installed without "
            "(on Debian/Ubuntu: sudo apt install python3-tk; "
            "on macOS with Homebrew: brew install python-tk)") from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise ReviewUnavailable(
            f"--review needs a desktop display to open its window ({exc})") from exc
    try:
        window = ReviewWindow(root, ReviewState(plan), action_verb, confirm)
        root.mainloop()
        return window.result
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
