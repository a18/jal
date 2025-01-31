from PySide6.QtCore import Signal, Slot, Property
from PySide6.QtWidgets import QDialog, QWidget, QPushButton, QComboBox, QMenu, QHBoxLayout, QCheckBox, QMessageBox
from jal.db.db import JalModel
from jal.db.account import JalAccount
from jal.db.asset import JalAsset
from jal.widgets.helpers import center_window
from jal.widgets.reference_dialogs import AccountListDialog
from jal.ui.ui_select_account_dlg import Ui_SelectAccountDlg


########################################################################################################################
#  UI Button to choose accounts
########################################################################################################################
class AccountButton(QPushButton):
    changed = Signal(int)

    def __init__(self, parent):
        super().__init__(parent=parent)
        self.p_account_id = 0

        self.Menu = QMenu(self)
        self.Menu.addAction(self.tr("Choose account"), self.ChooseAccount)
        self.Menu.addAction(self.tr("Any account"), self.ClearAccount)
        self.setMenu(self.Menu)

        self.dialog = AccountListDialog()
        self.setText(self.dialog.SelectedName)

    def getId(self):
        return self.p_account_id

    def setId(self, account_id):
        self.p_account_id = account_id
        if self.p_account_id:
            self.setText(JalAccount(account_id).name())
        else:
            self.setText(self.tr("ANY"))
        self.changed.emit(self.p_account_id)

    account_id = Property(int, getId, setId, notify=changed)

    def ChooseAccount(self):
        ref_point = self.mapToGlobal(self.geometry().bottomLeft())
        self.dialog.setGeometry(ref_point.x(), ref_point.y(), self.dialog.width(), self.dialog.height())
        self.dialog.setFilter()
        res = self.dialog.exec(enable_selection=True)
        if res:
            self.account_id = self.dialog.selected_id

    def ClearAccount(self):
        self.account_id = 0


#-----------------------------------------------------------------------------------------------------------------------
# Dialog for account selection
# Constructor takes description to show and recent_account for default choice.
# Selected account won't be equal to current_account
class SelectAccountDialog(QDialog):
    def __init__(self, description, current_account, recent_account=None):
        super().__init__()
        self.ui = Ui_SelectAccountDlg()
        self.ui.setupUi(self)
        self.account_id = recent_account
        self.store_account = False
        self.current_account = current_account

        self.ui.DescriptionLbl.setText(description)
        if self.account_id:
            self.ui.AccountWidget.selected_id = self.account_id
        center_window(self)

    @Slot()
    def closeEvent(self, event):
        self.account_id = self.ui.AccountWidget.selected_id
        self.store_account = self.ui.ReuseAccount.isChecked()
        if self.ui.AccountWidget.selected_id == 0:
            QMessageBox().warning(None, self.tr("No selection"), self.tr("Invalid account selected"), QMessageBox.Ok)
            event.ignore()
            return

        if self.ui.AccountWidget.selected_id == self.current_account:
            QMessageBox().warning(None, self.tr("No selection"), self.tr("Please select different account"),
                                  QMessageBox.Ok)
            event.ignore()
            return

        self.setResult(QDialog.Accepted)
        event.accept()


# ----------------------------------------------------------------------------------------------------------------------
class CurrencyComboBox(QComboBox):
    changed = Signal(int)

    def __init__(self, parent):
        super().__init__(parent=parent)
        self.p_selected_id = 0
        self.model = None
        self.activated.connect(self.OnUserSelection)

        self.model = JalModel(self, "currencies")
        self.model.select()
        self.setModel(self.model)
        self.setModelColumn(self.model.fieldIndex("symbol"))

    def getId(self):
        return self.p_selected_id

    def setId(self, new_id):
        if self.p_selected_id == new_id:
            return
        self.p_selected_id = new_id
        name = JalAsset(self.p_selected_id).symbol()
        if self.currentIndex() == self.findText(name):
            return
        self.setCurrentIndex(self.findText(name))

    selected_id = Property(int, getId, setId, notify=changed, user=True)

    def setIndex(self, index):
        if index is not None:
            self.selected_id = index
            self.changed.emit(self.selected_id)

    @Slot()
    def OnUserSelection(self, _selected_index):
        self.selected_id = self.model.record(self.currentIndex()).value("id")
        self.changed.emit(self.selected_id)


# ----------------------------------------------------------------------------------------------------------------------
class OptionalCurrencyComboBox(QWidget):
    changed = Signal()
    name_updated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._id = 0

        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.null_flag = QCheckBox(parent)
        self.null_flag.setChecked(False)
        self.null_flag.setText(self.tr("Currency"))
        self.layout.addWidget(self.null_flag)
        self.currency = CurrencyComboBox(parent)
        self.currency.setEnabled(False)
        self.layout.addWidget(self.currency)
        self.setLayout(self.layout)

        self.setFocusProxy(self.null_flag)
        self.null_flag.clicked.connect(self.onClick)
        self.currency.changed.connect(self.onCurrencyChange)

    def setText(self, text):
        self.null_flag.setText(text)

    def getId(self):
        return self._id if self._id else None

    def setId(self, new_value):
        if self._id == new_value:
            return
        self._id = new_value
        self.updateView()
        name = JalAsset(self._id).symbol()
        self.name_updated.emit('' if name is None else name)

    currency_id = Property(int, getId, setId, notify=changed, user=True)

    def updateView(self):
        has_value = True if self._id else False
        if has_value:
            self.currency.selected_id = self._id
        self.null_flag.setChecked(has_value)
        self.currency.setEnabled(has_value)

    @Slot()
    def onClick(self):
        if self.null_flag.isChecked():
            if self.currency.selected_id == 0:
                self.currency.selected_id = JalAsset.get_base_currency()
            self.currency_id = self.currency.selected_id
        else:
            self.currency_id = 0
        self.changed.emit()

    @Slot()
    def onCurrencyChange(self, _id):
        self.currency_id = self.currency.selected_id
        self.changed.emit()
