import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shopping_list import ShoppingList  # noqa: E402


class ShoppingListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "list.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_strips_and_ignores_blank(self):
        sl = ShoppingList()
        self.assertIsNone(sl.add("   "))
        item = sl.add("  Milk  ")
        self.assertEqual(item.name, "Milk")
        self.assertEqual(len(sl.items), 1)

    def test_toggle_and_remaining(self):
        sl = ShoppingList()
        a = sl.add("Eggs")
        sl.add("Bread")
        self.assertEqual(sl.remaining, 2)
        sl.toggle(a.id)
        self.assertTrue(a.checked)
        self.assertEqual(sl.remaining, 1)
        sl.toggle(a.id)
        self.assertFalse(a.checked)

    def test_checked_items_sort_last(self):
        sl = ShoppingList()
        a, b, c = sl.add("A"), sl.add("B"), sl.add("C")
        sl.set_checked(a.id, True)
        self.assertEqual([i.name for i in sl.sorted_items()], ["B", "C", "A"])

    def test_remove_and_clear_checked(self):
        sl = ShoppingList()
        a, b, c = sl.add("A"), sl.add("B"), sl.add("C")
        sl.remove(b.id)
        sl.set_checked(a.id, True)
        sl.clear_checked()
        self.assertEqual([i.name for i in sl.items], ["C"])

    def test_persists_across_instances(self):
        sl = ShoppingList(self.path)
        a = sl.add("Apples")
        sl.add("Pears")
        sl.toggle(a.id)
        reloaded = ShoppingList(self.path)
        self.assertEqual([(i.name, i.checked) for i in reloaded.items],
                         [("Apples", True), ("Pears", False)])

    def test_corrupt_file_starts_empty(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(ShoppingList(self.path).items, [])

    def test_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            ShoppingList().toggle("nope")


if __name__ == "__main__":
    unittest.main()
