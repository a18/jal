from functools import partial

from PySide6.QtCore import Qt, Slot, Signal, QDateTime, QSortFilterProxyModel
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox
from jal.ui.ui_operations_widget import Ui_OperationsWidget
from jal.widgets.mdi import MdiWidget
from jal.db.helpers import load_icon
from jal.db.account import JalAccount
from jal.db.asset import JalAsset
from jal.db.balances_model import BalancesModel
from jal.db.operations_model import OperationsModel
from jal.db.operations import LedgerTransaction


# ----------------------------------------------------------------------------------------------------------------------
class OperationsWidget(MdiWidget):
    dbUpdated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_OperationsWidget()
        self.ui.setupUi(self)

        self.current_index = None  # this is used in onOperationContextMenu() to track item for menu

        # Set icons
        self.ui.NewOperationBtn.setIcon(load_icon("new.png"))
        self.ui.CopyOperationBtn.setIcon(load_icon("copy.png"))
        self.ui.DeleteOperationBtn.setIcon(load_icon("delete.png"))

        # Operations view context menu
        self.contextMenu = QMenu(self.ui.OperationsTableView)
        self.actionReconcile = QAction(load_icon("reconcile.png"), self.tr("Reconcile"), self)
        self.actionCopy = QAction(load_icon("copy.png"), self.tr("Copy"), self)
        self.actionDelete = QAction(load_icon("delete.png"), self.tr("Delete"), self)
        self.contextMenu.addAction(self.actionReconcile)
        self.contextMenu.addSeparator()
        self.contextMenu.addAction(self.actionCopy)
        self.contextMenu.addAction(self.actionDelete)

        # Customize UI configuration
        self.balances_model = BalancesModel(self.ui.BalancesTableView)
        self.ui.BalancesTableView.setModel(self.balances_model)
        self.balances_model.configureView()

        self.operations_model = OperationsModel(self.ui.OperationsTableView)
        self.operations_filtered_model = QSortFilterProxyModel(self.ui.OperationsTableView)
        self.operations_filtered_model.setSourceModel(self.operations_model)
        self.operations_filtered_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.ui.OperationsTableView.setModel(self.operations_filtered_model)
        self.operations_model.configureView()
        self.ui.OperationsTableView.setContextMenuPolicy(Qt.CustomContextMenu)

        self.connect_signals_and_slots()

        self.NewOperationMenu = QMenu()
        self.ui.OperationsTabs.dbUpdated.connect(self.dbUpdated)
        self.ui.OperationsTabs.dbUpdated.connect(self.operations_model.refresh)
        for key, name in self.ui.OperationsTabs.get_operations_list().items():
            self.NewOperationMenu.addAction(name, partial(self.createOperation, key))
        self.ui.NewOperationBtn.setMenu(self.NewOperationMenu)

        # Setup balance and holdings parameters
        current_time = QDateTime.currentDateTime()
        current_time.setTimeSpec(Qt.UTC)  # We use UTC everywhere so need to force TZ info
        self.ui.BalanceDate.setDateTime(current_time)
        self.ui.BalancesCurrencyCombo.setIndex(JalAsset.get_base_currency())

        self.ui.OperationsTableView.selectRow(0)
        self.ui.DateRange.setCurrentIndex(0)

    def connect_signals_and_slots(self):
        self.actionReconcile.triggered.connect(self.reconcileAtCurrentOperation)
        self.ui.BalanceDate.dateChanged.connect(self.ui.BalancesTableView.model().setDate)
        self.ui.BalancesCurrencyCombo.changed.connect(self.ui.BalancesTableView.model().setCurrency)
        self.ui.BalancesTableView.doubleClicked.connect(self.OnBalanceDoubleClick)
        self.ui.ShowInactiveCheckBox.stateChanged.connect(self.ui.BalancesTableView.model().toggleActive)
        self.ui.DateRange.changed.connect(self.operations_model.setDateRange)
        self.ui.ChooseAccountBtn.changed.connect(self.operations_model.setAccount)
        self.ui.SearchString.editingFinished.connect(self.updateOperationsFilter)
        self.ui.OperationsTableView.selectionModel().selectionChanged.connect(self.OnOperationChange)
        self.ui.OperationsTableView.customContextMenuRequested.connect(self.onOperationContextMenu)
        self.ui.DeleteOperationBtn.clicked.connect(self.deleteOperation)
        self.actionDelete.triggered.connect(self.deleteOperation)
        self.ui.CopyOperationBtn.clicked.connect(self.ui.OperationsTabs.copy_operation)
        self.actionCopy.triggered.connect(self.ui.OperationsTabs.copy_operation)

    @Slot()
    def deleteOperation(self):
        if QMessageBox().warning(None, self.tr("Confirmation"),
                                 self.tr("Are you sure to delete selected transacion(s)?"),
                                 QMessageBox.Yes, QMessageBox.No) == QMessageBox.No:
            return
        rows = []
        for index in self.ui.OperationsTableView.selectionModel().selectedRows():
            rows.append(index.row())
        self.operations_model.deleteRows(rows)
        self.dbUpdated.emit()

    @Slot()
    def createOperation(self, operation_type):
        self.ui.OperationsTabs.new_operation(operation_type, self.operations_model.getAccount())

    @Slot()
    def updateOperationsFilter(self):
        self.ui.OperationsTableView.model().setFilterFixedString(self.ui.SearchString.text())
        self.ui.OperationsTableView.model().setFilterKeyColumn(-1)

    @Slot()
    def OnBalanceDoubleClick(self, index):
        self.ui.ChooseAccountBtn.account_id = index.model().getAccountId(index.row())

    @Slot()
    def OnOperationChange(self, selected, _deselected):
        op_type = LedgerTransaction.NA
        op_id = 0
        if len(self.ui.OperationsTableView.selectionModel().selectedRows()) == 1:
            idx = selected.indexes()
            if idx:
                selected_row = self.operations_filtered_model.mapToSource(idx[0]).row()
                op_type, op_id = self.operations_model.get_operation(selected_row)
        self.ui.OperationsTabs.show_operation(op_type, op_id)

    @Slot()
    def onOperationContextMenu(self, pos):
        self.current_index = self.ui.OperationsTableView.indexAt(pos)
        if len(self.ui.OperationsTableView.selectionModel().selectedRows()) != 1:
            self.actionReconcile.setEnabled(False)
            self.actionCopy.setEnabled(False)
        else:
            self.actionReconcile.setEnabled(True)
            self.actionCopy.setEnabled(True)
        self.contextMenu.popup(self.ui.OperationsTableView.viewport().mapToGlobal(pos))

    @Slot()
    def reconcileAtCurrentOperation(self):
        idx = self.operations_model.index(self.current_index.row(), 0)  # we need only row to address fields by name
        timestamp = self.operations_model.data(idx, Qt.UserRole, field="timestamp")
        account_id = self.operations_model.data(idx, Qt.UserRole, field="account_id")
        JalAccount(account_id).reconcile(timestamp)
        self.operations_model.refresh()

    def refresh(self):
        self.balances_model.update()
