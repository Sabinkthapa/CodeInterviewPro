"""Shopping List - a Kivy mobile app with check marks.

Run on desktop:  python main.py
Build for Android:  buildozer android debug   (see README.md)
"""

import os

from kivy.app import App
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout

from shopping_list import ShoppingList

KV = """
#:import dp kivy.metrics.dp

<ItemRow>:
    size_hint_y: None
    height: dp(56)
    padding: dp(8), 0
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: (0.93, 0.97, 0.93, 1) if root.checked else (1, 1, 1, 1)
        Rectangle:
            pos: self.pos
            size: self.size
        Color:
            rgba: 0.88, 0.88, 0.88, 1
        Rectangle:
            pos: self.x, self.y
            size: self.width, dp(1)
    CheckBox:
        size_hint_x: None
        width: dp(48)
        active: root.checked
        color: 0.18, 0.6, 0.3, 1
        on_active: root.on_check(self.active)
    Label:
        text: ("[s]%s[/s]" % root.escaped) if root.checked else root.escaped
        markup: True
        color: (0.55, 0.55, 0.55, 1) if root.checked else (0.1, 0.1, 0.1, 1)
        font_size: '18sp'
        halign: 'left'
        valign: 'middle'
        text_size: self.size
        shorten: True
    Button:
        text: 'X'
        size_hint_x: None
        width: dp(48)
        background_normal: ''
        background_color: 0.9, 0.3, 0.3, 1
        on_release: root.app.remove_item(root.item_id)

<Root>:
    orientation: 'vertical'
    canvas.before:
        Color:
            rgba: 0.97, 0.97, 0.97, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        size_hint_y: None
        height: dp(56)
        padding: dp(12), 0
        canvas.before:
            Color:
                rgba: 0.18, 0.6, 0.3, 1
            Rectangle:
                pos: self.pos
                size: self.size
        Label:
            text: 'Shopping List'
            bold: True
            font_size: '20sp'
            halign: 'left'
            valign: 'middle'
            text_size: self.size
        Label:
            id: counter
            size_hint_x: None
            width: dp(120)
            halign: 'right'
            valign: 'middle'
            text_size: self.size
    BoxLayout:
        size_hint_y: None
        height: dp(56)
        padding: dp(8)
        spacing: dp(8)
        TextInput:
            id: entry
            hint_text: 'Add an item...'
            multiline: False
            font_size: '18sp'
            on_text_validate: app.add_item(self.text)
        Button:
            text: 'Add'
            size_hint_x: None
            width: dp(80)
            background_normal: ''
            background_color: 0.18, 0.6, 0.3, 1
            on_release: app.add_item(entry.text)
    ScrollView:
        do_scroll_x: False
        BoxLayout:
            id: items_box
            orientation: 'vertical'
            size_hint_y: None
            height: self.minimum_height
    Button:
        text: 'Clear checked items'
        size_hint_y: None
        height: dp(52)
        background_normal: ''
        background_color: 0.35, 0.35, 0.35, 1
        on_release: app.clear_checked()
"""


def escape_markup(text):
    return text.replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;")


class ItemRow(BoxLayout):
    item_id = StringProperty()
    name = StringProperty()
    escaped = StringProperty()
    checked = BooleanProperty(False)
    app = ObjectProperty()

    def on_name(self, _, value):
        self.escaped = escape_markup(value)

    def on_check(self, active):
        if active != self.checked:
            self.app.set_checked(self.item_id, active)


class Root(BoxLayout):
    pass


class ShoppingListApp(App):
    title = "Shopping List"

    def build(self):
        Builder.load_string(KV)
        path = os.path.join(self.user_data_dir, "shopping_list.json")
        self.model = ShoppingList(path)
        self.root_widget = Root()
        self.refresh()
        return self.root_widget

    # --- actions ---------------------------------------------------------
    def add_item(self, text):
        if self.model.add(text):
            self.root_widget.ids.entry.text = ""
            self.refresh()
        self.root_widget.ids.entry.focus = True

    def set_checked(self, item_id, checked):
        self.model.set_checked(item_id, checked)
        self.refresh()

    def remove_item(self, item_id):
        self.model.remove(item_id)
        self.refresh()

    def clear_checked(self):
        self.model.clear_checked()
        self.refresh()

    # --- view ------------------------------------------------------------
    def refresh(self):
        box = self.root_widget.ids.items_box
        box.clear_widgets()
        for item in self.model.sorted_items():
            box.add_widget(
                ItemRow(item_id=item.id, name=item.name, checked=item.checked, app=self)
            )
        total = len(self.model.items)
        self.root_widget.ids.counter.text = (
            f"{self.model.remaining} of {total} left" if total else ""
        )


if __name__ == "__main__":
    ShoppingListApp().run()
