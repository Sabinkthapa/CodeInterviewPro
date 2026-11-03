"""Tests for dedupe_photos.py."""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import dedupe_photos as dp


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_image(path: Path, variant: int = 0, size=(400, 300), fmt=None) -> Path:
    """Draw a simple picture; different `variant`s look clearly different."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(img)
    if variant == 0:
        draw.ellipse((50, 50, 250, 250), fill="red")
        draw.rectangle((0, 0, 60, 40), fill="black")
    elif variant == 1:
        draw.rectangle((150, 20, 390, 280), fill="blue")
        draw.line((0, 300, 400, 0), fill="black", width=20)
    else:
        for x in range(0, 400, 40):
            draw.rectangle((x, 0, x + 20, 300), fill="green")
    if size != (400, 300):
        img = img.resize(size)
    img.save(path, format=fmt, quality=95) if fmt in (None, "JPEG") \
        else img.save(path, format=fmt)
    return path


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def as_sets(groups):
    return sorted((sorted(map(str, g)) for g in groups))


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A folder with one group of 3 exact copies and one unique photo."""
    root = tmp_path / "photos"
    original = make_image(root / "a.jpg", 0)
    data = original.read_bytes()
    write_bytes(root / "b.jpg", data)
    write_bytes(root / "sub" / "c.jpg", data)
    make_image(root / "unique.jpg", 1)
    # make a.jpg the oldest so the default --keep oldest keeps it
    for i, name in enumerate(["a.jpg", "b.jpg", "sub/c.jpg", "unique.jpg"]):
        os.utime(root / name, (1_000_000 + i, 1_000_000 + i))
    return root


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #

class TestIterImages:
    def test_filters_by_extension_case_insensitive(self, tmp_path):
        write_bytes(tmp_path / "a.JPG", b"x")
        write_bytes(tmp_path / "b.png", b"x")
        write_bytes(tmp_path / "notes.txt", b"x")
        found = {p.name for p in dp.iter_images([tmp_path], True, dp.IMAGE_EXTENSIONS)}
        assert found == {"a.JPG", "b.png"}

    def test_recursive_and_non_recursive(self, tmp_path):
        write_bytes(tmp_path / "top.jpg", b"x")
        write_bytes(tmp_path / "sub" / "deep.jpg", b"x")
        rec = {p.name for p in dp.iter_images([tmp_path], True, dp.IMAGE_EXTENSIONS)}
        flat = {p.name for p in dp.iter_images([tmp_path], False, dp.IMAGE_EXTENSIONS)}
        assert rec == {"top.jpg", "deep.jpg"}
        assert flat == {"top.jpg"}

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_skips_symlinks(self, tmp_path):
        real = write_bytes(tmp_path / "real.jpg", b"x")
        (tmp_path / "link.jpg").symlink_to(real)
        found = [p.name for p in dp.iter_images([tmp_path], True, dp.IMAGE_EXTENSIONS)]
        assert found == ["real.jpg"]

    def test_overlapping_roots_counted_once(self, tmp_path):
        write_bytes(tmp_path / "sub" / "a.jpg", b"x")
        found = list(dp.iter_images([tmp_path, tmp_path / "sub"], True,
                                    dp.IMAGE_EXTENSIONS))
        assert len(found) == 1

    def test_accepts_single_file(self, tmp_path):
        f = write_bytes(tmp_path / "a.jpg", b"x")
        assert list(dp.iter_images([f], True, dp.IMAGE_EXTENSIONS)) == [f.resolve()]


# --------------------------------------------------------------------------- #
# Exact duplicates
# --------------------------------------------------------------------------- #

class TestExactDuplicates:
    def test_groups_identical_files(self, tmp_path):
        a = write_bytes(tmp_path / "a.jpg", b"same content")
        b = write_bytes(tmp_path / "b.jpg", b"same content")
        c = write_bytes(tmp_path / "c.jpg", b"different!!!")  # same size
        groups = dp.find_exact_duplicates([a, b, c])
        assert as_sets(groups) == [sorted([str(a), str(b)])]

    def test_same_prefix_different_tail_not_grouped(self, tmp_path):
        prefix = b"\0" * (dp.PARTIAL_SIZE + 10)
        a = write_bytes(tmp_path / "a.jpg", prefix + b"A")
        b = write_bytes(tmp_path / "b.jpg", prefix + b"B")
        assert dp.find_exact_duplicates([a, b]) == []

    def test_multiple_groups(self, tmp_path):
        files = [write_bytes(tmp_path / f"{n}{i}.jpg", n.encode() * 50)
                 for n in ("x", "y") for i in range(2)]
        assert len(dp.find_exact_duplicates(files)) == 2

    def test_no_files(self):
        assert dp.find_exact_duplicates([]) == []

    def test_hash_file_with_limit(self, tmp_path):
        f = write_bytes(tmp_path / "a.jpg", b"abcdef")
        g = write_bytes(tmp_path / "b.jpg", b"abcXYZ")
        assert dp._hash_file(f, 3) == dp._hash_file(g, 3)
        assert dp._hash_file(f) != dp._hash_file(g)


# --------------------------------------------------------------------------- #
# Similar duplicates
# --------------------------------------------------------------------------- #

def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class TestSimilarDuplicates:
    def test_dhash_is_64_bit_and_stable(self, tmp_path):
        f = make_image(tmp_path / "a.jpg")
        h = dp.dhash(f)
        assert 0 <= h < 2 ** 64
        assert dp.dhash(f) == h

    def test_resized_copy_has_close_hash(self, tmp_path):
        big = make_image(tmp_path / "big.jpg", 0)
        small = make_image(tmp_path / "small.png", 0, size=(200, 150), fmt="PNG")
        other = make_image(tmp_path / "other.jpg", 1)
        assert hamming(dp.dhash(big), dp.dhash(small)) <= 5
        assert hamming(dp.dhash(big), dp.dhash(other)) > 10

    def test_dhash_respects_exif_rotation(self, tmp_path):
        upright = make_image(tmp_path / "upright.jpg", 0)
        with Image.open(upright) as img:
            rotated = img.rotate(90, expand=True)  # pixels turned 90 CCW
            rotated.save(tmp_path / "no_exif.jpg", quality=95)
            exif = Image.Exif()
            exif[0x0112] = 6  # Orientation: viewer must rotate 90 CW
            rotated.save(tmp_path / "rotated.jpg", exif=exif, quality=95)
        up = dp.dhash(upright)
        assert hamming(up, dp.dhash(tmp_path / "no_exif.jpg")) > 10
        assert hamming(up, dp.dhash(tmp_path / "rotated.jpg")) <= 5

    def test_groups_resized_copies(self, tmp_path):
        big = make_image(tmp_path / "big.jpg", 0)
        small = make_image(tmp_path / "small.png", 0, size=(200, 150), fmt="PNG")
        other = make_image(tmp_path / "other.jpg", 1)
        groups = dp.find_similar_duplicates([big, small, other], threshold=5)
        assert as_sets(groups) == [sorted([str(big), str(small)])]

    def test_threshold_zero_requires_identical_hash(self, tmp_path):
        a = make_image(tmp_path / "a.jpg", 0)
        b = write_bytes(tmp_path / "b.jpg", a.read_bytes())
        c = make_image(tmp_path / "c.jpg", 2)
        groups = dp.find_similar_duplicates([a, b, c], threshold=0)
        assert as_sets(groups) == [sorted([str(a), str(b)])]

    def test_transitive_grouping(self, tmp_path, monkeypatch):
        # a~b and b~c within threshold, a and c not: all three end up together
        files = [tmp_path / n for n in ("a.jpg", "b.jpg", "c.jpg")]
        hashes = {files[0]: 0b0000, files[1]: 0b0011, files[2]: 0b1111}
        monkeypatch.setattr(dp, "dhash", lambda p: hashes[p])
        groups = dp.find_similar_duplicates(files, threshold=2)
        assert as_sets(groups) == [sorted(map(str, files))]

    def test_unreadable_image_is_skipped(self, tmp_path, capsys):
        good = make_image(tmp_path / "good.jpg")
        bad = write_bytes(tmp_path / "bad.jpg", b"not an image")
        assert dp.find_similar_duplicates([good, bad], threshold=5) == []
        assert "skipping" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Keep strategies
# --------------------------------------------------------------------------- #

class TestSplitKeep:
    @pytest.fixture
    def trio(self, tmp_path):
        old = make_image(tmp_path / "deep" / "folder" / "old.jpg", 0, size=(100, 75))
        mid = make_image(tmp_path / "mid.jpg", 0, size=(400, 300))
        new = make_image(tmp_path / "x" / "new.jpg", 0, size=(200, 150))
        for t, f in ((1_000, old), (2_000, mid), (3_000, new)):
            os.utime(f, (t, t))
        return old, mid, new

    @pytest.mark.parametrize("strategy,expected", [
        ("oldest", 0), ("newest", 2), ("shortest-path", 1), ("largest", 1),
    ])
    def test_strategies(self, trio, strategy, expected):
        keep, dupes = dp.split_keep(list(trio), strategy)
        assert keep == trio[expected]
        assert set(dupes) == set(trio) - {keep}

    def test_tie_is_deterministic(self, tmp_path):
        a = write_bytes(tmp_path / "a.jpg", b"x")
        b = write_bytes(tmp_path / "b.jpg", b"x")
        for f in (a, b):
            os.utime(f, (5, 5))
        assert dp.split_keep([b, a], "oldest")[0] == a
        assert dp.split_keep([a, b], "oldest")[0] == a


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def test_unique_destination(tmp_path):
    assert dp._unique_destination(tmp_path, "a.jpg") == tmp_path / "a.jpg"
    (tmp_path / "a.jpg").touch()
    (tmp_path / "a_1.jpg").touch()
    assert dp._unique_destination(tmp_path, "a.jpg") == tmp_path / "a_2.jpg"


@pytest.mark.parametrize("n,text", [
    (0, "0.0 B"), (1023, "1023.0 B"), (1024, "1.0 KB"),
    (5 * 1024 ** 2, "5.0 MB"), (3 * 1024 ** 4, "3.0 TB"),
])
def test_human_size(n, text):
    assert dp.human_size(n) == text


# --------------------------------------------------------------------------- #
# Command line (end to end)
# --------------------------------------------------------------------------- #

def remaining(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


class TestCli:
    def test_dry_run_changes_nothing(self, library, capsys):
        before = remaining(library)
        assert dp.main([str(library)]) == 0
        out = capsys.readouterr().out
        assert remaining(library) == before
        assert "Dry run" in out
        assert "1 group(s), 2 duplicate file(s)" in out

    def test_delete_with_yes(self, library):
        assert dp.main([str(library), "--delete", "-y"]) == 0
        assert remaining(library) == {"a.jpg", "unique.jpg"}

    def test_delete_asks_and_aborts(self, library, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        before = remaining(library)
        assert dp.main([str(library), "--delete"]) == 1
        assert remaining(library) == before

    def test_delete_asks_and_proceeds(self, library, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
        assert dp.main([str(library), "--delete"]) == 0
        assert remaining(library) == {"a.jpg", "unique.jpg"}

    def test_keep_newest(self, library):
        dp.main([str(library), "--delete", "-y", "--keep", "newest"])
        assert remaining(library) == {"sub/c.jpg", "unique.jpg"}

    def test_move_to_and_rescan_ignores_trash(self, library):
        trash = library / "trash"
        assert dp.main([str(library), "--move-to", str(trash), "-y"]) == 0
        assert remaining(library) == {"a.jpg", "unique.jpg",
                                      "trash/b.jpg", "trash/c.jpg"}
        # the trash folder sits inside the scanned folder but must be ignored
        assert dp.main([str(library), "--move-to", str(trash), "-y"]) == 0
        assert len(remaining(trash)) == 2

    def test_move_to_handles_name_clash(self, tmp_path):
        data = make_image(tmp_path / "src" / "one" / "img.jpg").read_bytes()
        write_bytes(tmp_path / "src" / "two" / "img.jpg", data)
        write_bytes(tmp_path / "src" / "three" / "img.jpg", data)
        trash = tmp_path / "trash"
        dp.main([str(tmp_path / "src"), "--move-to", str(trash), "-y"])
        assert remaining(trash) == {"img.jpg", "img_1.jpg"}

    def test_similar_mode_end_to_end(self, tmp_path):
        root = tmp_path / "p"
        make_image(root / "big.jpg", 0)
        make_image(root / "small.png", 0, size=(200, 150), fmt="PNG")
        make_image(root / "other.jpg", 1)
        assert dp.main([str(root), "--mode", "similar", "--keep", "largest",
                        "--delete", "-y"]) == 0
        assert remaining(root) == {"big.jpg", "other.jpg"}

    def test_report_csv(self, library, tmp_path):
        report = tmp_path / "report.csv"
        dp.main([str(library), "--report", str(report)])
        with report.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert [r["action"] for r in rows] == ["keep", "remove", "remove"]
        assert {r["group"] for r in rows} == {"1"}
        assert Path(rows[0]["path"]).name == "a.jpg"
        assert all(int(r["size_bytes"]) > 0 for r in rows)

    def test_extensions_filter(self, library):
        dp.main([str(library), "--extensions", "png", "--delete", "-y"])
        assert remaining(library) == {"a.jpg", "b.jpg", "sub/c.jpg", "unique.jpg"}

    def test_no_recursive(self, library):
        dp.main([str(library), "--no-recursive", "--delete", "-y"])
        assert remaining(library) == {"a.jpg", "sub/c.jpg", "unique.jpg"}

    def test_no_duplicates(self, tmp_path, capsys):
        make_image(tmp_path / "a.jpg", 0)
        make_image(tmp_path / "b.jpg", 1)
        assert dp.main([str(tmp_path)]) == 0
        assert "No duplicates found" in capsys.readouterr().out

    def test_failed_removal_returns_2(self, library, monkeypatch):
        def boom(*args, **kwargs):
            raise PermissionError("read-only")
        monkeypatch.setattr(dp, "remove_file", boom)
        assert dp.main([str(library), "--delete", "-y"]) == 2
        assert len(remaining(library)) == 4

    @pytest.mark.parametrize("args", [
        ["--threshold", "65"],
        ["--threshold", "-1"],
        ["--delete", "--move-to", "x"],
        ["--trash", "--delete"],
        ["--trash", "--move-to", "x"],
    ])
    def test_invalid_arguments(self, library, args):
        with pytest.raises(SystemExit) as exc:
            dp.main([str(library), *args])
        assert exc.value.code == 2

    def test_missing_path(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            dp.main([str(tmp_path / "nope")])
        assert exc.value.code == 2


# --------------------------------------------------------------------------- #
# Send to Trash
# --------------------------------------------------------------------------- #

@pytest.fixture
def fake_trash(monkeypatch, tmp_path):
    """Replace the send2trash module with one that records what it trashed."""
    import types

    bin_dir = tmp_path / "fake-trash-bin"
    trashed: list[str] = []

    def send2trash(path):
        trashed.append(os.path.basename(path))
        bin_dir.mkdir(exist_ok=True)
        os.replace(path, bin_dir / f"{len(trashed)}_{os.path.basename(path)}")

    module = types.ModuleType("send2trash")
    module.send2trash = send2trash
    monkeypatch.setitem(sys.modules, "send2trash", module)
    return trashed


@pytest.fixture
def no_send2trash(monkeypatch):
    # A None entry in sys.modules makes `import send2trash` raise ImportError.
    monkeypatch.setitem(sys.modules, "send2trash", None)


class TestRemoveFile:
    def test_delete(self, tmp_path):
        f = write_bytes(tmp_path / "a.jpg", b"x")
        dp.remove_file(f, "delete")
        assert not f.exists()

    def test_move(self, tmp_path):
        f = write_bytes(tmp_path / "a.jpg", b"x")
        dest = tmp_path / "out"
        dp.remove_file(f, "move", dest)
        assert not f.exists()
        assert (dest / "a.jpg").read_bytes() == b"x"

    def test_trash(self, tmp_path, fake_trash):
        f = write_bytes(tmp_path / "a.jpg", b"x")
        dp.remove_file(f, "trash")
        assert not f.exists()
        assert fake_trash == ["a.jpg"]

    def test_move_without_destination(self, tmp_path):
        f = write_bytes(tmp_path / "a.jpg", b"x")
        with pytest.raises(ValueError):
            dp.remove_file(f, "move")
        assert f.exists()

    def test_unknown_method(self, tmp_path):
        f = write_bytes(tmp_path / "a.jpg", b"x")
        with pytest.raises(ValueError):
            dp.remove_file(f, "shred")
        assert f.exists()


class TestTrashCli:
    def test_trash_with_yes(self, library, fake_trash, capsys):
        assert dp.main([str(library), "--trash", "-y"]) == 0
        assert remaining(library) == {"a.jpg", "unique.jpg"}
        assert sorted(fake_trash) == ["b.jpg", "c.jpg"]
        assert "Sent to Trash: 2 file(s)." in capsys.readouterr().out

    def test_trash_asks_first(self, library, fake_trash, monkeypatch):
        prompts = []
        monkeypatch.setattr("builtins.input",
                            lambda prompt: prompts.append(prompt) or "n")
        before = remaining(library)
        assert dp.main([str(library), "--trash"]) == 1
        assert remaining(library) == before
        assert fake_trash == []
        assert "send 2 file(s) to the Trash" in prompts[0]

    def test_dry_run_mentions_trash(self, library, capsys):
        dp.main([str(library)])
        assert "--trash" in capsys.readouterr().out

    def test_missing_send2trash_fails_before_changing_anything(
            self, library, no_send2trash):
        before = remaining(library)
        with pytest.raises(SystemExit) as exc:
            dp.main([str(library), "--trash", "-y"])
        assert "pip install send2trash" in str(exc.value.code)
        assert remaining(library) == before

    def test_dry_run_and_delete_work_without_send2trash(
            self, library, no_send2trash):
        assert dp.main([str(library)]) == 0
        assert dp.main([str(library), "--delete", "-y"]) == 0
        assert remaining(library) == {"a.jpg", "unique.jpg"}

    def test_trash_failure_is_reported(self, library, monkeypatch, capsys):
        import types

        def refuse(path):
            raise OSError("trash not available")
        module = types.ModuleType("send2trash")
        module.send2trash = refuse
        monkeypatch.setitem(sys.modules, "send2trash", module)
        assert dp.main([str(library), "--trash", "-y"]) == 2
        assert len(remaining(library)) == 4
        assert "trash not available" in capsys.readouterr().err

    @pytest.mark.skipif(not sys.platform.startswith("linux"),
                        reason="uses the freedesktop.org Trash layout")
    def test_real_send2trash_on_linux(self, library, tmp_path, monkeypatch):
        data_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        # send2trash reads XDG_DATA_HOME when it is imported, so drop any copy
        # already loaded; otherwise files would go to the user's real Trash.
        for name in [m for m in sys.modules if m.split(".")[0] == "send2trash"]:
            monkeypatch.delitem(sys.modules, name)
        pytest.importorskip("send2trash")
        assert dp.main([str(library), "--trash", "-y"]) == 0
        assert remaining(library) == {"a.jpg", "unique.jpg"}
        trash = data_home / "Trash"
        assert sorted(p.name for p in (trash / "files").iterdir()) \
            == ["b.jpg", "c.jpg"]
        # .trashinfo files record where each photo came from, for restoring
        infos = sorted((trash / "info").iterdir())
        assert len(infos) == 2
        assert all("Path=" in i.read_text() for i in infos)
