"""Shopping list model: items with a checked state, persisted as JSON.

Kept free of any UI imports so it can be unit-tested without Kivy.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field


@dataclass
class Item:
    name: str
    checked: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


class ShoppingList:
    def __init__(self, path=None):
        self.path = path
        self.items = []
        if path and os.path.exists(path):
            self.load()

    # --- mutations -------------------------------------------------------
    def add(self, name):
        name = name.strip()
        if not name:
            return None
        item = Item(name=name)
        self.items.append(item)
        self.save()
        return item

    def toggle(self, item_id):
        item = self.get(item_id)
        item.checked = not item.checked
        self.save()
        return item

    def set_checked(self, item_id, checked):
        item = self.get(item_id)
        item.checked = bool(checked)
        self.save()
        return item

    def remove(self, item_id):
        self.items = [i for i in self.items if i.id != item_id]
        self.save()

    def clear_checked(self):
        self.items = [i for i in self.items if not i.checked]
        self.save()

    # --- queries ---------------------------------------------------------
    def get(self, item_id):
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(item_id)

    def sorted_items(self):
        """Unchecked items first, checked items sink to the bottom."""
        return sorted(self.items, key=lambda i: i.checked)

    @property
    def remaining(self):
        return sum(1 for i in self.items if not i.checked)

    # --- persistence -----------------------------------------------------
    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(i) for i in self.items], f, indent=2)
        os.replace(tmp, self.path)  # atomic: never leaves a half-written file

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.items = [Item(**d) for d in data]
        except (OSError, ValueError, TypeError):
            self.items = []
