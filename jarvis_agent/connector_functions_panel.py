"""Reusable function choices for a connector. Presentation only, no grants.

The caller supplies the observed functions and their groups. This widget knows
neither a provider nor an authentication method and never contacts a service.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout


class FunctionChoice(QCheckBox):
    def nextCheckState(self):
        # A mixed group becomes fully selected with one click. A user cannot
        # accidentally cycle into a partial grant that has no clear meaning.
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class ConnectorFunctionsPanel(QFrame):
    selection_changed = Signal(list)

    def __init__(self, groups, parent=None):
        super().__init__(parent)
        self.groups = dict(groups)
        self.choices = {}
        self._tools, self._selected = [], set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        for group, title in self.groups.items():
            choice = FunctionChoice(title)
            choice.setAccessibleName(title)
            choice.stateChanged.connect(lambda state, key=group: self._choose(key, state))
            layout.addWidget(choice)
            self.choices[group] = choice

    def load(self, tools, selected):
        self._tools = [dict(t) for t in tools if t.get("group") in self.groups]
        self.set_selection(selected)

    def set_selection(self, names):
        self._selected = set(names) & {t["name"] for t in self._tools}
        for group, choice in self.choices.items():
            names = {t["name"] for t in self._tools if t["group"] == group}
            count = len(names & self._selected)
            choice.blockSignals(True)
            choice.setCheckState(Qt.Unchecked if not count else Qt.Checked if count == len(names) else Qt.PartiallyChecked)
            choice.blockSignals(False)
            choice.setVisible(bool(names))
            choice.setToolTip(f"{count} fonction(s) choisie(s) sur {len(names)} disponible(s).")
        self.summary.setText(f"{len(self._selected)} fonction(s) choisie(s) sur {len(self._tools)} disponible(s).")

    def selected(self):
        return [t["name"] for t in self._tools if t["name"] in self._selected]

    def _choose(self, group, state):
        names = {t["name"] for t in self._tools if t["group"] == group}
        chosen = self._selected | names if state == Qt.Checked.value else self._selected - names
        self.set_selection(chosen)
        self.selection_changed.emit(self.selected())
