"""Tests for the --review window (review_window.py) and its CLI wiring."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import dedupe_photos as dp
import review_window as rw
from review_window import ReviewState


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_image(path: Path, color="red", size=(320, 240)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).ellipse((40, 40, size[0] - 40, size[1] - 40), fill=color)
    img.save(path, quality=95)
    return path


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def remaining(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def files(tmp_path):
    """Two groups: a/b/c (3 copies of one photo) and d/e (2 copies of another)."""
    a = make_image(tmp_path / "a.jpg", "red")
    b = write_bytes(tmp_path / "b.jpg", a.read_bytes())
    c = write_bytes(tmp_path / "sub" / "c.jpg", a.read_bytes())
    d = make_image(tmp_path / "d.jpg", "blue")
    e = write_bytes(tmp_path / "e.jpg", d.read_bytes())
    return a, b, c, d, e


@pytest.fixture
def plan(files):
    a, b, c, d, e = files
    return [([a], [b, c]), ([d], [e])]


# --------------------------------------------------------------------------- #
# ReviewState (no display needed)
# --------------------------------------------------------------------------- #

class TestReviewState:
    def test_starts_from_plan(self, plan, files):
        a, b, c, d, e = files
        state = ReviewState(plan)
        assert len(state) == 2
        assert state.files[0] == [a, b, c]
        assert state.keep[0] == [True, False, False]
        assert state.removals(0) == [b, c]
        assert state.total_remove_count() == 3

    def test_group_without_keep_is_rejected(self, files):
        with pytest.raises(ValueError):
            ReviewState([([], list(files[:2]))])

    def test_toggle(self, plan):
        state = ReviewState(plan)
        assert state.toggle(0, 1)
        assert state.keep[0] == [True, True, False]
        assert state.toggle(0, 1)
        assert state.keep[0] == [True, False, False]

    def test_cannot_unkeep_last_photo(self, plan):
        state = ReviewState(plan)
        assert not state.toggle(0, 0)
        assert not state.set_keep(0, 0, False)
        assert state.keep[0] == [True, False, False]
        # after keeping another one, the first may be removed
        state.toggle(0, 2)
        assert state.toggle(0, 0)
        assert state.keep[0] == [False, False, True]

    def test_keeping_is_always_allowed(self, plan):
        state = ReviewState(plan)
        assert state.set_keep(0, 0, True)
        assert state.set_keep(0, 1, True)

    def test_keep_only_keep_all_reset(self, plan):
        state = ReviewState(plan)
        state.keep_only(0, 2)
        assert state.keep[0] == [False, False, True]
        state.keep_all(0)
        assert state.keep[0] == [True, True, True]
        assert state.removals(0) == []
        state.reset(0)
        assert state.keep[0] == [True, False, False]
        assert state.keep[1] == [True, False]  # other groups untouched

    def test_byte_totals(self, plan, files):
        a, b, c, d, e = files
        state = ReviewState(plan)
        assert state.group_remove_bytes(0) == b.stat().st_size + c.stat().st_size
        assert state.total_remove_bytes() == (b.stat().st_size + c.stat().st_size
                                              + e.stat().st_size)

    def test_missing_file_counts_as_zero_bytes(self, tmp_path, files):
        gone = tmp_path / "gone.jpg"
        state = ReviewState([([files[0]], [gone])])
        assert state.total_remove_bytes() == 0

    def test_result(self, plan, files):
        a, b, c, d, e = files
        state = ReviewState(plan)
        state.toggle(0, 1)       # keep b too
        state.keep_only(1, 1)    # keep e instead of d
        assert state.result() == [([a, b], [c]), ([e], [d])]


def test_common_and_short_folder(tmp_path):
    a, b = tmp_path / "x" / "2023" / "a.jpg", tmp_path / "x" / "b.jpg"
    base = rw.common_folder([a, b])
    assert base == tmp_path / "x"
    assert rw.short_folder(a, base) == "2023"
    assert rw.short_folder(b, base) == "(top folder)"
    assert rw.short_folder(a, None) == str(a.parent)
    assert rw.short_folder(tmp_path / "elsewhere.jpg", base) == str(tmp_path)


def test_human_size():
    assert rw.human_size(512) == "512.0 B"
    assert rw.human_size(3 * 1024 ** 2) == "3.0 MB"


def test_load_thumbnail(tmp_path):
    photo = make_image(tmp_path / "p.jpg", size=(800, 400))
    img, dims = rw.load_thumbnail(photo, 200)
    assert dims == (800, 400)
    assert max(img.size) == 200
    assert rw.load_thumbnail(write_bytes(tmp_path / "bad.jpg", b"nope"), 200) \
        == (None, None)


def test_load_thumbnail_respects_exif_rotation(tmp_path):
    img = Image.new("RGB", (400, 200), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # display rotated 90 degrees
    img.save(tmp_path / "r.jpg", exif=exif)
    _, dims = rw.load_thumbnail(tmp_path / "r.jpg", 100)
    assert dims == (200, 400)


def test_run_review_without_display(monkeypatch, plan):
    tk = pytest.importorskip("tkinter")

    def no_display(*args, **kwargs):
        raise tk.TclError("no display name and no $DISPLAY environment variable")
    monkeypatch.setattr(tk, "Tk", no_display)
    with pytest.raises(rw.ReviewUnavailable, match="desktop display"):
        rw.run_review(plan, "Send to Trash")


def test_run_review_with_nothing_to_review():
    assert rw.run_review([], "Send to Trash") == []


# --------------------------------------------------------------------------- #
# The window (needs a display; CI runs Linux under Xvfb)
# --------------------------------------------------------------------------- #

@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root, plan):
    asked = []

    def ask(title, question):
        asked.append(question)
        return window.answer

    window = rw.ReviewWindow(tk_root, ReviewState(plan), "Send to Trash", ask=ask)
    window.answer = True
    window.asked = asked
    tk_root.update_idletasks()
    return window


class TestReviewWindow:
    def test_shows_first_group(self, window, files):
        assert window.title_label.cget("text") == "Group 1 of 2  ·  3 photos"
        assert [c["path"] for c in window.cards] == list(files[:3])
        assert [c["status"].cget("text") for c in window.cards] \
            == ["KEEP", "REMOVE", "REMOVE"]
        assert window.apply_button.cget("text") == "Send to Trash: 3 file(s)"
        assert "Keeping 1, removing 2" in window.group_label.cget("text")

    def test_loads_thumbnails_and_details(self, window, files):
        assert all(window._thumbs[p] is not None for p in files[:3])
        assert window._dims[files[0]] == (320, 240)
        assert window.base == files[0].parent
        texts = [w.cget("text") for w in window.cards[2]["frame"].winfo_children()
                 if w.winfo_class() in ("TLabel", "Label")]
        assert "c.jpg" in texts and "sub" in texts
        assert any(t.startswith("320×240 · ") for t in texts)

    def test_broken_image_shows_placeholder(self, tk_root, files, tmp_path):
        bad = write_bytes(tmp_path / "bad.jpg", b"not an image")
        win = rw.ReviewWindow(tk_root, ReviewState([([files[0]], [bad])]), "Go")
        assert win._thumbs[bad] is None
        assert len(win.cards) == 2

    def test_toggle_updates_labels(self, window):
        window.toggle(1)
        assert window.cards[1]["status"].cget("text") == "KEEP"
        assert window.cards[1]["var"].get() is True
        assert window.apply_button.cget("text") == "Send to Trash: 2 file(s)"
        assert window.cards[1]["frame"].cget("highlightbackground") == rw.KEEP_COLOR

    def test_refuses_to_remove_last_kept_photo(self, window):
        window.toggle(0)
        assert window.state.keep[0][0] is True
        assert window.cards[0]["var"].get() is True
        assert "at least one" in window.message.cget("text")
        window.toggle(1)  # a valid change clears the message
        assert window.message.cget("text") == ""

    def test_checkbox(self, window):
        window.cards[2]["var"].set(True)
        window._on_check(2)
        assert window.state.keep[0] == [True, False, True]
        window.cards[0]["var"].set(False)
        window._on_check(0)
        assert window.state.keep[0] == [False, False, True]

    def test_toggle_out_of_range_is_ignored(self, window):
        window.toggle(8)  # pressing "9" in a group of 3
        assert window.state.keep[0] == [True, False, False]

    def test_keep_only_keep_all_reset(self, window):
        window.keep_only(2)
        assert window.state.keep[0] == [False, False, True]
        window.keep_all()
        assert window.apply_button.cget("text") == "Send to Trash: 1 file(s)"
        window.reset_group()
        assert window.state.keep[0] == [True, False, False]

    def test_navigation(self, window, files):
        assert "disabled" in window.prev_button.state()
        window.next_group()
        assert window.title_label.cget("text") == "Group 2 of 2  ·  2 photos"
        assert [c["path"] for c in window.cards] == list(files[3:])
        assert "disabled" in window.next_button.state()
        window.next_group()  # already at the end
        assert window.group == 1
        window.prev_group()
        assert window.group == 0

    def test_choices_survive_navigation(self, window):
        window.toggle(1)
        window.next_group()
        window.prev_group()
        assert window.cards[1]["status"].cget("text") == "KEEP"

    def test_apply_asks_then_returns_choices(self, window, files):
        a, b, c, d, e = files
        window.toggle(1)
        window.apply()
        assert len(window.asked) == 1
        assert "2 file(s)" in window.asked[0]
        assert window.result == [([a, b], [c]), ([d], [e])]

    def test_apply_declined_keeps_window_open(self, window):
        window.answer = False
        window.apply()
        assert window.result is None
        assert window.cards  # still showing

    def test_apply_without_confirmation(self, tk_root, plan):
        def ask(*args):
            raise AssertionError("should not ask")
        win = rw.ReviewWindow(tk_root, ReviewState(plan), "Go", confirm=False, ask=ask)
        win.apply()
        assert win.result == ReviewState(plan).result()

    def test_apply_with_nothing_to_remove_does_not_ask(self, window):
        window.keep_all()
        window.next_group()
        window.keep_all()
        window.apply()
        assert window.asked == []
        assert all(not removes for _, removes in window.result)

    def test_cancel(self, window):
        window.toggle(1)
        window.cancel()
        assert window.result is None


def test_run_review_real_window(monkeypatch, plan, files, tk_root):
    """Open the real window through run_review and click Apply from a timer."""
    a, b, c, d, e = files

    class AutoClick(rw.ReviewWindow):
        def __init__(self, root, state, verb, confirm=True, **kwargs):
            super().__init__(root, state, verb, confirm,
                             ask=lambda *_: True, **kwargs)
            root.after(50, lambda: (self.toggle(2), self.apply()))

    monkeypatch.setattr(rw, "ReviewWindow", AutoClick)
    assert rw.run_review(plan, "Send to Trash") == [([a, c], [b]), ([d], [e])]


# --------------------------------------------------------------------------- #
# --review on the command line
# --------------------------------------------------------------------------- #

@pytest.fixture
def library(tmp_path):
    root = tmp_path / "photos"
    a = make_image(root / "a.jpg", "red")
    write_bytes(root / "b.jpg", a.read_bytes())
    write_bytes(root / "sub" / "c.jpg", a.read_bytes())
    make_image(root / "unique.jpg", "blue")
    for i, name in enumerate(["a.jpg", "b.jpg", "sub/c.jpg", "unique.jpg"]):
        os.utime(root / name, (1_000_000 + i, 1_000_000 + i))
    return root


def fake_review(choose):
    """A run_review stand-in: `choose` edits a ReviewState like a user would."""
    calls = []

    def run_review(plan, verb, confirm=True):
        calls.append({"plan": plan, "verb": verb, "confirm": confirm})
        state = ReviewState(plan)
        if choose is None:
            return None
        choose(state)
        return state.result()
    run_review.calls = calls
    return run_review


def no_input(prompt):
    raise AssertionError("the terminal prompt should be skipped after review")


class TestReviewCli:
    def test_review_needs_an_action(self, library):
        with pytest.raises(SystemExit) as exc:
            dp.main([str(library), "--review"])
        assert exc.value.code == 2

    def test_applies_reviewed_choices(self, library, monkeypatch):
        review = fake_review(lambda s: s.toggle(0, 1))  # also keep b.jpg
        monkeypatch.setattr(rw, "run_review", review)
        monkeypatch.setattr("builtins.input", no_input)
        assert dp.main([str(library), "--review", "--delete"]) == 0
        assert remaining(library) == {"a.jpg", "b.jpg", "unique.jpg"}
        assert review.calls[0]["verb"] == "Delete permanently"
        assert review.calls[0]["confirm"] is True

    def test_default_choice_is_passed_to_window(self, library, monkeypatch):
        review = fake_review(lambda s: None)
        monkeypatch.setattr(rw, "run_review", review)
        dp.main([str(library), "--review", "--delete", "-y", "--keep", "newest"])
        (keeps, removes), = review.calls[0]["plan"]
        assert [p.name for p in keeps] == ["c.jpg"]
        assert review.calls[0]["confirm"] is False  # -y skips the dialog too
        assert remaining(library) == {"sub/c.jpg", "unique.jpg"}

    def test_cancel_changes_nothing(self, library, monkeypatch, capsys):
        monkeypatch.setattr(rw, "run_review", fake_review(None))
        before = remaining(library)
        assert dp.main([str(library), "--review", "--delete"]) == 1
        assert remaining(library) == before
        assert "Review cancelled" in capsys.readouterr().out

    def test_keep_everything(self, library, monkeypatch, capsys):
        monkeypatch.setattr(rw, "run_review", fake_review(lambda s: s.keep_all(0)))
        before = remaining(library)
        assert dp.main([str(library), "--review", "--delete"]) == 0
        assert remaining(library) == before
        assert "Nothing to remove" in capsys.readouterr().out

    def test_move_to_verb(self, library, tmp_path, monkeypatch):
        review = fake_review(lambda s: None)
        monkeypatch.setattr(rw, "run_review", review)
        trash = tmp_path / "out"
        dp.main([str(library), "--review", "--move-to", str(trash)])
        assert review.calls[0]["verb"] == f"Move to {trash.resolve()}"
        assert len(remaining(trash)) == 2

    def test_report_reflects_review(self, library, tmp_path, monkeypatch):
        import csv
        monkeypatch.setattr(rw, "run_review", fake_review(lambda s: s.toggle(0, 1)))
        report = tmp_path / "r.csv"
        dp.main([str(library), "--review", "--delete", "--report", str(report)])
        with report.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [(Path(r["path"]).name, r["action"]) for r in rows] \
            == [("a.jpg", "keep"), ("b.jpg", "keep"), ("c.jpg", "remove")]

    def test_no_display_exits_with_message(self, library, monkeypatch):
        def unavailable(*args, **kwargs):
            raise rw.ReviewUnavailable("--review needs a desktop display")
        monkeypatch.setattr(rw, "run_review", unavailable)
        before = remaining(library)
        with pytest.raises(SystemExit) as exc:
            dp.main([str(library), "--review", "--delete"])
        assert "desktop display" in str(exc.value.code)
        assert remaining(library) == before
