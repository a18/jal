from functools import partial
import time
from datetime import datetime
import logging

from jal.constants import TransactionType, CorporateAction
from jal.reports.helpers import XLSX, xslxFormat, xlsxWriteRow, xlsxWriteZeros
from jal.reports.dlsg import DLSG
from jal.ui_custom.helpers import g_tr
from jal.db.helpers import executeSQL, readSQLrecord, readSQL
from PySide2.QtWidgets import QDialog, QFileDialog
from PySide2.QtCore import Property, Slot
from jal.ui.ui_tax_export_dlg import Ui_TaxExportDlg


#-----------------------------------------------------------------------------------------------------------------------
class TaxExportDialog(QDialog, Ui_TaxExportDlg):
    def __init__(self, parent, db):
        QDialog.__init__(self)
        self.setupUi(self)

        self.AccountWidget.init_db(db)
        self.XlsSelectBtn.pressed.connect(partial(self.OnFileBtn, 'XLS-OUT'))
        self.InitialSelectBtn.pressed.connect(partial(self.OnFileBtn, 'DLSG-IN'))
        self.OutputSelectBtn.pressed.connect(partial(self.OnFileBtn, 'DLSG-OUT'))

        # center dialog with respect to parent window
        x = parent.x() + parent.width()/2 - self.width()/2
        y = parent.y() + parent.height()/2 - self.height()/2
        self.setGeometry(x, y, self.width(), self.height())

    @Slot()
    def OnFileBtn(self, type):
        selector = {
            'XLS-OUT': (g_tr('TaxExportDialog', "Save tax reports to:"),
                        g_tr('TaxExportDialog', "Excel files (*.xlsx)"),
                        '.xlsx', self.XlsFileName),
            'DLSG-IN': (g_tr('TaxExportDialog', "Get tax form template from:"),
                        g_tr('TaxExportDialog', "Tax form 2020 (*.dc0)"),
                        '.dc0', self.DlsgInFileName),
            'DLSG-OUT': (g_tr('TaxExportDialog', "Save tax form to:"),
                        g_tr('TaxExportDialog', "Tax form 2020 (*.dc0)"),
                        '.dc0', self.DlsgOutFileName),
        }
        if type[-3:] == '-IN':
            filename = QFileDialog.getOpenFileName(self, selector[type][0], ".", selector[type][1])
        elif type[-4:] == '-OUT':
            filename = QFileDialog.getSaveFileName(self, selector[type][0], ".", selector[type][1])
        else:
            raise ValueError
        if filename[0]:
            if filename[1] == selector[type][1] and filename[0][-len(selector[type][2]):] != selector[type][2]:
                selector[type][3].setText(filename[0] + selector[type][2])
            else:
                selector[type][3].setText(filename[0])

    def getYear(self):
        return self.Year.value()

    def getXlsFilename(self):
        return self.XlsFileName.text()

    def getAccount(self):
        return self.AccountWidget.selected_id

    def getDlsgState(self):
        return self.DlsgGroup.isChecked()

    def getDslgInFilename(self):
        return self.DlsgInFileName.text()

    def getDslgOutFilename(self):
        return self.DlsgOutFileName.text()

    def getDividendsOnly(self):
        return self.DividendsOnly.isChecked()

    def getNoSettlement(self):
        return self.NoSettlement.isChecked()

    year = Property(int, fget=getYear)
    xls_filename = Property(str, fget=getXlsFilename)
    account = Property(int, fget=getAccount)
    update_dlsg = Property(bool, fget=getDlsgState)
    dlsg_in_filename = Property(str, fget=getDslgInFilename)
    dlsg_out_filename = Property(str, fget=getDslgOutFilename)
    dlsg_dividends_only = Property(bool, fget=getDividendsOnly)
    no_settelement = Property(bool, fget=getNoSettlement)


#-----------------------------------------------------------------------------------------------------------------------
class TaxesRus:
    RPT_METHOD = 0
    RPT_TITLE = 1
    RPT_COLUMNS = 2

    CorpActionText = {
        CorporateAction.SymbolChange: "Смена символа {old} -> {new}",
        CorporateAction.Split: "Сплит {old} {before} в {after}",
        CorporateAction.SpinOff: "Выделение компании {after} {new} из {before} {old}",
        CorporateAction.Merger: "Слияние компании, конвертация {before} {old} в {after} {new}",
        CorporateAction.StockDividend: "Допэмиссия акций: {after} {new}"
    }

    def __init__(self, db):
        self.db = db
        self.account_currency = ''
        self.broker_name = ''
        self.use_settlement = True
        self.current_report = None
        self.reports = {
            "Дивиденды": (self.prepare_dividends,
                          "Отчет по дивидендам, полученным в отчетном периоде",
                          {
                              "Дата выплаты": 10,
                              "Ценная бумага": 8,
                              "Полное наименование": 50,
                              "Курс USD/RUB на дату выплаты": 16,
                              "Доход, USD": 12,
                              "Доход, RUB (код 1010)": 12,
                              "Налог упл., USD": 12,
                              "Налог упл., RUB": 12,
                              "Налог к уплате, RUB": 12,
                              "Страна": 20,
                              "СОИДН": 7
                          }),
            "Сделки с ЦБ": (self.prepare_trades,
                            "Отчет по сделкам с ценными бумагами, завершённым в отчетном периоде",
                            {
                                "Ценная бумага": 8,
                                "Кол-во": 8,
                                "Тип сделки": 8,
                                "Дата сделки": 10,
                                "Курс USD/RUB на дату сделки": 9,
                                "Дата поставки": 10,
                                "Курс USD/RUB на дату поставки": 9,
                                "Цена, USD": 12,
                                "Сумма сделки, USD": 12,
                                "Сумма сделки, RUB": 12,
                                "Комиссия, USD": 12,
                                "Комиссия, RUB": 9,
                                "Доход, RUB (код 1530)": 12,
                                "Расход, RUB (код 201)": 12,
                                "Финансовый результат, RUB": 12,
                                "Финансовый результат, USD": 12
                            }),
            "Сделки с ПФИ": (self.prepare_derivatives,
                             "Отчет по сделкам с производными финансовыми инструментами, завершённым в отчетном периоде",
                             {
                                 "Ценная бумага": 22,
                                 "Кол-во": 8,
                                 "Тип сделки": 8,
                                 "Дата сделки": 10,
                                 "Курс USD/RUB на дату сделки": 9,
                                 "Дата поставки": 10,
                                 "Курс USD/RUB на дату поставки": 9,
                                 "Цена, USD": 12,
                                 "Сумма сделки, USD": 12,
                                 "Сумма сделки, RUB": 12,
                                 "Комиссия, USD": 12,
                                 "Комиссия, RUB": 9,
                                 "Доход, RUB (код 1532)": 12,
                                 "Расход, RUB (код 206)": 12,
                                 "Финансовый результат, RUB": 12,
                                 "Финансовый результат, USD": 12
                             }),
            "Комиссии": (self.prepare_broker_fees,
                         "Отчет по комиссиям, уплаченным брокеру в отчетном периоде",
                         {
                             "Описание": 50,
                             "Сумма, USD": 8,
                             "Дата оплаты": 10,
                             "Курс USD/RUB на дату оплаты": 10,
                             "Сумма, RUB": 10
                         }),
            "Корп.события": (self.prepare_corporate_actions,
                             "Отчет по сделкам с ценными бумагами, завершённым в отчетном периоде "
                             "с предшествовавшими корпоративными событиями",
                             {
                                 "Операция": 20,
                                 "Дата сделки": 10,
                                 "Ценная бумага": 8,
                                 "Кол-во": 8,
                                 "Курс USD/RUB на дату сделки": 9,
                                 "Дата поставки": 10,
                                 "Курс USD/RUB на дату поставки": 9,
                                 "Цена, USD": 12,
                                 "Сумма сделки, USD": 12,
                                 "Сумма сделки, RUB": 12,
                                 "Комиссия, USD": 12,
                                 "Комиссия, RUB": 9,

                             })
        }
        self.bool_text = {
            0: 'Нет',
            1: 'Да'
        }
        self.reports_xls = None
        self.statement = None
        self.current_sheet = None

    def showTaxesDialog(self, parent):
        dialog = TaxExportDialog(parent, self.db)
        if dialog.exec_():
            self.use_settlement = not dialog.no_settelement
            self.save2file(dialog.xls_filename, dialog.year, dialog.account, dlsg_update=dialog.update_dlsg,
                           dlsg_in=dialog.dlsg_in_filename, dlsg_out=dialog.dlsg_out_filename,
                           dlsg_dividends_only=dialog.dlsg_dividends_only)

    def save2file(self, taxes_file, year, account_id,
                  dlsg_update=False, dlsg_in=None, dlsg_out=None, dlsg_dividends_only=False):
        self.account_currency = readSQL(self.db,
                                        "SELECT c.name FROM accounts AS a "
                                        "LEFT JOIN assets AS c ON a.currency_id = c.id WHERE a.id=:account",
                                        [(":account", account_id)])
        self.broker_name = readSQL(self.db,
                                   "SELECT b.name FROM accounts AS a "
                                   "LEFT JOIN agents AS b ON a.organization_id = b.id WHERE a.id=:account",
                                   [(":account", account_id)])
        year_begin = int(time.mktime(datetime.strptime(f"{year}", "%Y").timetuple()))
        year_end = int(time.mktime(datetime.strptime(f"{year + 1}", "%Y").timetuple()))

        self.reports_xls = XLSX(taxes_file)

        self.statement = None
        if dlsg_update:
            self.statement = DLSG(only_dividends=dlsg_dividends_only)
            try:
                self.statement.read_file(dlsg_in)
            except:
                logging.error(g_tr('TaxesRus', "Can't open tax form file ") + f"'{dlsg_in}'")
                return

        self.account_id = account_id
        self.prepare_exchange_rate_dates()
        for report in self.reports:
            self.current_report = report
            self.current_sheet = self.reports_xls.add_report_sheet(report)
            self.add_report_header()
            self.prepare_report(account_id, year_begin, year_end)

        self.reports_xls.save()

        if dlsg_update:
            try:
                self.statement.write_file(dlsg_out)
            except:
                logging.error(g_tr('TaxesRus', "Can't write tax form into file ") + f"'{dlsg_out}'")


    # This method puts header on each report sheet
    def add_report_header(self):
        report = self.reports[self.current_report]
        self.current_sheet.write(0, 0, report[self.RPT_TITLE], self.reports_xls.formats.Bold())
        self.current_sheet.write(2, 0, "Документ-основание:")
        self.current_sheet.write(3, 0, "Период:")
        self.current_sheet.write(4, 0, "ФИО:")
        self.current_sheet.write(5, 0, "Номер счета:")  # TODO insert account number

        header_row = {}
        for i, column in enumerate(report[self.RPT_COLUMNS]):
            # make tuple for each column i: ("Column_Title", xlsx.formats.ColumnHeader(), Column_Width, 0, 0)
            header_row[i] = (column, self.reports_xls.formats.ColumnHeader(), report[self.RPT_COLUMNS][column], 0, 0)
        xlsxWriteRow(self.current_sheet, 7, header_row, 60)

        for column in range(len(header_row)):  # Put column numbers for reference
            header_row[column] = (f"({column + 1})", self.reports_xls.formats.ColumnHeader())
        xlsxWriteRow(self.current_sheet, 8, header_row)

    # Exchange rates are present in database not for every date (and not every possible timestamp)
    # As any action has exact timestamp it won't match rough timestamp of exchange rate most probably
    # Function fills 't_last_dates' table with correspondence between 'real' timestamp and nearest 'exchange' timestamp
    def prepare_exchange_rate_dates(self):
        _ = executeSQL(self.db, "DELETE FROM t_last_dates")
        _ = executeSQL(self.db,
                       "INSERT INTO t_last_dates(ref_id, timestamp) "
                       "SELECT ref_id, coalesce(MAX(q.timestamp), 0) AS timestamp "
                       "FROM (SELECT d.timestamp AS ref_id "
                       "FROM dividends AS d "
                       "WHERE d.account_id = :account_id "
                       "UNION "
                       "SELECT a.timestamp AS ref_id "
                       "FROM actions AS a "
                       "WHERE a.account_id = :account_id "
                       "UNION "
                       "SELECT t.timestamp AS ref_id "
                       "FROM deals AS d "
                       "LEFT JOIN sequence AS s ON (s.id=d.open_sid OR s.id=d.close_sid) AND s.type = 3 "
                       "LEFT JOIN trades AS t ON s.operation_id=t.id "
                       "WHERE d.account_id = :account_id "
                       "UNION "
                       "SELECT c.settlement AS ref_id "
                       "FROM deals AS d "
                       "LEFT JOIN sequence AS s ON (s.id=d.open_sid OR s.id=d.close_sid) AND s.type = 3 "
                       "LEFT JOIN trades AS c ON s.operation_id=c.id "
                       "WHERE d.account_id = :account_id "
                       "UNION "
                       "SELECT o.timestamp AS ref_id "
                       "FROM deals AS d "
                       "LEFT JOIN sequence AS s ON (s.id=d.open_sid OR s.id=d.close_sid) AND s.type = 5 "
                       "LEFT JOIN corp_actions AS o ON s.operation_id=o.id "
                       "WHERE d.account_id = :account_id) "
                       "LEFT JOIN accounts AS a ON a.id = :account_id "
                       "LEFT JOIN quotes AS q ON ref_id >= q.timestamp AND a.currency_id=q.asset_id "
                       "WHERE ref_id IS NOT NULL "
                       "GROUP BY ref_id", [(":account_id", self.account_id)])
        self.db.commit()
# -----------------------------------------------------------------------------------------------------------------------
    def prepare_report(self, account_id, year_begin, year_end):
        report = self.reports[self.current_report]
        report[self.RPT_METHOD](self.reports_xls, account_id, year_begin, year_end)

# -----------------------------------------------------------------------------------------------------------------------
    def prepare_dividends(self, xlsx, account_id, begin, end):
        sheet = self.current_sheet

        query = executeSQL(self.db,
                           "SELECT d.timestamp AS payment_date, s.name AS symbol, s.full_name AS full_name, "
                           "d.sum AS amount, d.sum_tax AS tax_amount, q.quote AS rate_cbr , "
                           "c.name AS country, c.code AS country_code, c.tax_treaty AS tax_treaty "
                           "FROM dividends AS d "
                           "LEFT JOIN assets AS s ON s.id = d.asset_id "
                           "LEFT JOIN accounts AS a ON d.account_id = a.id "
                           "LEFT JOIN countries AS c ON d.tax_country_id = c.id "
                           "LEFT JOIN t_last_dates AS ld ON d.timestamp=ld.ref_id "
                           "LEFT JOIN quotes AS q ON ld.timestamp=q.timestamp AND a.currency_id=q.asset_id "
                           "WHERE d.timestamp>=:begin AND d.timestamp<:end AND d.account_id=:account_id "
                           "ORDER BY d.timestamp",
                           [(":begin", begin), (":end", end), (":account_id", account_id)])
        row = start_row = 9
        while query.next():
            payment_date, symbol, full_name, amount_usd, tax_usd, rate, country, code, tax_treaty = readSQLrecord(query)
            amount_rub = round(amount_usd * rate, 2) if rate else 0
            tax_us_rub = round(tax_usd * rate, 2) if rate else 0
            tax_ru_rub = round(0.13 * amount_rub, 2)
            if tax_treaty:
                if tax_ru_rub > tax_us_rub:
                    tax_ru_rub = tax_ru_rub - tax_us_rub
                else:
                    tax_ru_rub = 0
            xlsxWriteRow(sheet, row, {
                0: (datetime.fromtimestamp(payment_date).strftime('%d.%m.%Y'), xlsx.formats.Text(row)),
                1: (symbol, xlsx.formats.Text(row)),
                2: (full_name, xlsx.formats.Text(row)),
                3: (rate, xlsx.formats.Number(row, 4)),
                4: (amount_usd, xlsx.formats.Number(row, 2)),
                5: (amount_rub, xlsx.formats.Number(row, 2)),
                6: (tax_usd, xlsx.formats.Number(row, 2)),
                7: (tax_us_rub, xlsx.formats.Number(row, 2)),
                8: (tax_ru_rub, xlsx.formats.Number(row, 2)),
                9: (country, xlsx.formats.Text(row)),
                10: (self.bool_text[tax_treaty], xlsx.formats.Text(row))
            })
            code = 'us' if code == 'xx' else code   # TODO select right country code if it is absent
            if self.statement is not None:
                self.statement.add_dividend(code, f"{symbol} ({full_name})", payment_date, self.account_currency,
                                       amount_usd, amount_rub, tax_usd, tax_us_rub, rate)
            row += 1

        xlsx.add_totals_footer(sheet, start_row, row, [3, 4, 5, 6, 7, 8])

# -----------------------------------------------------------------------------------------------------------------------
    def prepare_trades(self, xlsx, account_id, begin, end):
        sheet = self.current_sheet

        # Take all actions without conversion
        query = executeSQL(self.db,
                           "SELECT s.name AS symbol, d.qty AS qty, "
                           "o.timestamp AS o_date, qo.quote AS o_rate, o.settlement AS os_date, "
                           "qos.quote AS os_rate, o.price AS o_price, o.qty AS o_qty, o.fee AS o_fee, "
                           "c.timestamp AS c_date, qc.quote AS c_rate, c.settlement AS cs_date, "
                           "qcs.quote AS cs_rate, c.price AS c_price, c.qty AS c_qty, c.fee AS c_fee "
                           "FROM deals AS d "
                           "JOIN sequence AS os ON os.id=d.open_sid AND os.type = 3 "  
                           "LEFT JOIN trades AS o ON os.operation_id=o.id "
                           "JOIN sequence AS cs ON cs.id=d.close_sid AND cs.type = 3 "
                           "LEFT JOIN trades AS c ON cs.operation_id=c.id "
                           "LEFT JOIN assets AS s ON o.asset_id=s.id "
                           "LEFT JOIN accounts AS a ON a.id = :account_id "
                           "LEFT JOIN t_last_dates AS ldo ON o.timestamp=ldo.ref_id "
                           "LEFT JOIN quotes AS qo ON ldo.timestamp=qo.timestamp AND a.currency_id=qo.asset_id "
                           "LEFT JOIN t_last_dates AS ldos ON o.settlement=ldos.ref_id "
                           "LEFT JOIN quotes AS qos ON ldos.timestamp=qos.timestamp AND a.currency_id=qos.asset_id "
                           "LEFT JOIN t_last_dates AS ldc ON c.timestamp=ldc.ref_id "
                           "LEFT JOIN quotes AS qc ON ldc.timestamp=qc.timestamp AND a.currency_id=qc.asset_id "
                           "LEFT JOIN t_last_dates AS ldcs ON c.settlement=ldcs.ref_id "
                           "LEFT JOIN quotes AS qcs ON ldcs.timestamp=qcs.timestamp AND a.currency_id=qcs.asset_id "
                           "WHERE c.timestamp>=:begin AND c.timestamp<:end AND d.account_id=:account_id "
                           "AND s.type_id >= 2 AND s.type_id <= 4 "  # To select only stocks/bonds/ETFs
                           "ORDER BY o.timestamp, c.timestamp",
                           [(":begin", begin), (":end", end), (":account_id", account_id)])
        start_row = 9
        data_row = 0
        while query.next():
            deal = readSQLrecord(query, named=True)
            row = start_row + (data_row * 2)
            if not self.use_settlement:
                deal['os_rate'] = deal['o_rate']
                deal['cs_rate'] = deal['c_rate']
            o_deal_type = "Покупка" if deal['qty'] >= 0 else "Продажа"
            c_deal_type = "Продажа" if deal['qty'] >= 0 else "Покупка"
            o_amount_usd = round(deal['o_price'] * abs(deal['qty']), 2)
            o_amount_rub = round(o_amount_usd * deal['o_rate'], 2) if deal['o_rate'] else 0
            c_amount_usd = round(deal['c_price'] * abs(deal['qty']), 2)
            c_amount_rub = round(c_amount_usd * deal['c_rate'], 2) if deal['c_rate'] else 0
            o_fee_usd = deal['o_fee'] * abs(deal['qty']/deal['o_qty'])
            c_fee_usd = deal['c_fee'] * abs(deal['qty'] / deal['c_qty'])
            o_fee_rub = round(o_fee_usd * deal['os_rate'], 2) if deal['os_rate'] else 0
            c_fee_rub = round(c_fee_usd * deal['cs_rate'], 2) if deal['cs_rate'] else 0
            income = c_amount_rub if deal['qty'] >= 0 else o_amount_rub
            income_usd = c_amount_usd if deal['qty']>=0 else o_amount_usd
            spending = o_amount_rub if deal['qty'] >= 0 else c_amount_rub
            spending_usd = o_amount_usd if deal['qty']>=0 else c_amount_usd
            spending = spending + o_fee_rub + c_fee_rub
            spending_usd = spending_usd + o_fee_usd + c_fee_usd
            xlsxWriteRow(sheet, row, {
                0: (deal['symbol'], xlsx.formats.Text(data_row), 0, 0, 1),
                1: (float(abs(deal['qty'])), xlsx.formats.Number(data_row, 0, True), 0, 0, 1),
                2: (o_deal_type, xlsx.formats.Text(data_row)),
                3: (datetime.fromtimestamp(deal['o_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                4: (deal['os_rate'], xlsx.formats.Number(data_row, 4)),
                5: (datetime.fromtimestamp(deal['os_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                6: (deal['o_rate'], xlsx.formats.Number(data_row, 4)),
                7: (deal['o_price'], xlsx.formats.Number(data_row, 6)),
                8: (o_amount_usd, xlsx.formats.Number(data_row, 2)),
                9: (o_amount_rub, xlsx.formats.Number(data_row, 2)),
                10: (o_fee_usd, xlsx.formats.Number(data_row, 6)),
                11: (o_fee_rub, xlsx.formats.Number(data_row, 2)),
                12: (income, xlsx.formats.Number(data_row, 2), 0, 0, 1),
                13: (spending, xlsx.formats.Number(data_row, 2), 0, 0, 1),
                14: (income - spending, xlsx.formats.Number(data_row, 2), 0, 0, 1),
                15: (income_usd - spending_usd, xlsx.formats.Number(data_row, 2), 0, 0, 1)
            })
            xlsxWriteRow(sheet, row + 1, {
                2: (c_deal_type, xlsx.formats.Text(data_row)),
                3: (datetime.fromtimestamp(deal['c_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                4: (deal['cs_rate'], xlsx.formats.Number(data_row, 4)),
                5: (datetime.fromtimestamp(deal['cs_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                6: (deal['c_rate'], xlsx.formats.Number(data_row, 4)),
                7: (deal['c_price'], xlsx.formats.Number(data_row, 6)),
                8: (c_amount_usd, xlsx.formats.Number(data_row, 2)),
                9: (c_amount_rub, xlsx.formats.Number(data_row, 2)),
                10: (c_fee_usd, xlsx.formats.Number(data_row, 6)),
                11: (c_fee_rub, xlsx.formats.Number(data_row, 2))
            })
            # TODO replace 'us' with value depandable on broker account
            if self.statement is not None:
                self.statement.add_stock_profit('us', self.broker_name, deal['c_date'], self.account_currency,
                                           income_usd, income, spending, deal['c_rate'])
            data_row = data_row + 1
        row = start_row + (data_row * 2)

        xlsx.add_totals_footer(sheet, start_row, row, [11, 12, 13, 14, 15])

# -----------------------------------------------------------------------------------------------------------------------
    def prepare_derivatives(self, xlsx, account_id, begin, end):
        sheet = self.current_sheet

        # Take all actions without conversion
        query = executeSQL(self.db,
                           "SELECT s.name AS symbol, d.qty AS qty, "
                           "o.timestamp AS o_date, qo.quote AS o_rate, o.settlement AS os_date, "
                           "qos.quote AS os_rate, o.price AS o_price, o.qty AS o_qty, o.fee AS o_fee, "
                           "c.timestamp AS c_date, qc.quote AS c_rate, c.settlement AS cs_date, "
                           "qcs.quote AS cs_rate, c.price AS c_price, c.qty AS c_qty, c.fee AS c_fee "
                           "FROM deals AS d "
                           "JOIN sequence AS os ON os.id=d.open_sid AND os.type = 3 "
                           "LEFT JOIN trades AS o ON os.operation_id=o.id "
                           "JOIN sequence AS cs ON cs.id=d.close_sid AND cs.type = 3 "
                           "LEFT JOIN trades AS c ON cs.operation_id=c.id "
                           "LEFT JOIN assets AS s ON o.asset_id=s.id "
                           "LEFT JOIN accounts AS a ON a.id = :account_id "
                           "LEFT JOIN t_last_dates AS ldo ON o.timestamp=ldo.ref_id "
                           "LEFT JOIN quotes AS qo ON ldo.timestamp=qo.timestamp AND a.currency_id=qo.asset_id "
                           "LEFT JOIN t_last_dates AS ldos ON o.settlement=ldos.ref_id "
                           "LEFT JOIN quotes AS qos ON ldos.timestamp=qos.timestamp AND a.currency_id=qos.asset_id "
                           "LEFT JOIN t_last_dates AS ldc ON c.timestamp=ldc.ref_id "
                           "LEFT JOIN quotes AS qc ON ldc.timestamp=qc.timestamp AND a.currency_id=qc.asset_id "
                           "LEFT JOIN t_last_dates AS ldcs ON c.settlement=ldcs.ref_id "
                           "LEFT JOIN quotes AS qcs ON ldcs.timestamp=qcs.timestamp AND a.currency_id=qcs.asset_id "
                           "WHERE c.timestamp>=:begin AND c.timestamp<:end AND d.account_id=:account_id "
                           "AND s.type_id == 6 "  # To select only derivatives
                           "ORDER BY o.timestamp, c.timestamp",
                           [(":begin", begin), (":end", end), (":account_id", account_id)])
        start_row = 9
        data_row = 0
        while query.next():
            deal = readSQLrecord(query, named=True)
            row = start_row + (data_row * 2)
            if not self.use_settlement:
                deal['os_rate'] = deal['o_rate']
                deal['cs_rate'] = deal['c_rate']
            o_deal_type = "Покупка" if deal['qty'] >= 0 else "Продажа"
            c_deal_type = "Продажа" if deal['qty'] >= 0 else "Покупка"
            o_amount_usd = round(deal['o_price'] * abs(deal['qty']), 2)
            o_amount_rub = round(o_amount_usd * deal['o_rate'], 2) if deal['o_rate'] else 0
            c_amount_usd = round(deal['c_price'] * abs(deal['qty']), 2)
            c_amount_rub = round(c_amount_usd * deal['c_rate'], 2) if deal['c_rate'] else 0
            o_fee_usd = deal['o_fee'] * abs(deal['qty'] / deal['o_qty'])
            c_fee_usd = deal['c_fee'] * abs(deal['qty'] / deal['c_qty'])
            o_fee_rub = round(o_fee_usd * deal['os_rate'], 2) if deal['os_rate'] else 0
            c_fee_rub = round(c_fee_usd * deal['cs_rate'], 2) if deal['cs_rate'] else 0
            income = c_amount_rub if deal['qty'] >= 0 else o_amount_rub
            income_usd = c_amount_usd if deal['qty'] >= 0 else o_amount_usd
            spending = o_amount_rub if deal['qty'] >= 0 else c_amount_rub
            spending_usd = o_amount_usd if deal['qty'] >= 0 else c_amount_usd
            spending = spending + o_fee_rub + c_fee_rub
            spending_usd = spending_usd + o_fee_usd + c_fee_usd
            xlsxWriteRow(sheet, row, {
                0: (deal['symbol'], xlsx.formats.Text(data_row), 0, 0, 1),
                1: (float(abs(deal['qty'])), xlsx.formats.Number(data_row, 0, True), 0, 0, 1),
                2: (o_deal_type, xlsx.formats.Text(data_row)),
                3: (datetime.fromtimestamp(deal['o_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                4: (deal['os_rate'], xlsx.formats.Number(data_row, 4)),
                5: (datetime.fromtimestamp(deal['os_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                6: (deal['o_rate'], xlsx.formats.Number(data_row, 4)),
                7: (deal['o_price'], xlsx.formats.Number(data_row, 6)),
                8: (o_amount_usd, xlsx.formats.Number(data_row, 2)),
                9: (o_amount_rub, xlsx.formats.Number(data_row, 2)),
                10: (o_fee_usd, xlsx.formats.Number(data_row, 6)),
                11: (o_fee_rub, xlsx.formats.Number(data_row, 2)),
                12: (income, xlsx.formats.Number(data_row, 2), 0, 0, 1),
                13: (spending, xlsx.formats.Number(data_row, 2), 0, 0, 1),
                14: (income - spending, xlsx.formats.Number(data_row, 2), 0, 0, 1),
                15: (income_usd - spending_usd, xlsx.formats.Number(data_row, 2), 0, 0, 1)
            })
            xlsxWriteRow(sheet, row + 1, {
                2: (c_deal_type, xlsx.formats.Text(data_row)),
                3: (datetime.fromtimestamp(deal['c_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                4: (deal['cs_rate'], xlsx.formats.Number(data_row, 4)),
                5: (datetime.fromtimestamp(deal['cs_date']).strftime('%d.%m.%Y'), xlsx.formats.Text(data_row)),
                6: (deal['c_rate'], xlsx.formats.Number(data_row, 4)),
                7: (deal['c_price'], xlsx.formats.Number(data_row, 6)),
                8: (c_amount_usd, xlsx.formats.Number(data_row, 2)),
                9: (c_amount_rub, xlsx.formats.Number(data_row, 2)),
                10: (c_fee_usd, xlsx.formats.Number(data_row, 6)),
                11: (c_fee_rub, xlsx.formats.Number(data_row, 2))
            })
            # TODO replace 'us' with value depandable on broker account
            if self.statement is not None:
                self.statement.add_derivative_profit('us', self.broker_name, deal['c_date'], self.account_currency,
                                                income_usd, income, spending, deal['c_rate'])
            data_row = data_row + 1
        row = start_row + (data_row * 2)

        xlsx.add_totals_footer(sheet, start_row, row, [11, 12, 13, 14, 15])


# -----------------------------------------------------------------------------------------------------------------------
    def prepare_broker_fees(self, xlsx, account_id, begin, end):
        sheet = self.current_sheet

        query = executeSQL(self.db,
                           "SELECT a.timestamp AS payment_date, d.sum AS amount, d.note AS note, q.quote AS rate_cbr "
                           "FROM actions AS a "
                           "LEFT JOIN action_details AS d ON d.pid=a.id "
                           "LEFT JOIN accounts AS c ON c.id = :account_id "
                           "LEFT JOIN t_last_dates AS ld ON a.timestamp=ld.ref_id "
                           "LEFT JOIN quotes AS q ON ld.timestamp=q.timestamp AND c.currency_id=q.asset_id "
                           "WHERE a.timestamp>=:begin AND a.timestamp<:end AND a.account_id=:account_id",
                           [(":begin", begin), (":end", end), (":account_id", account_id)])
        row = start_row = 9
        while query.next():
            payment_date, amount, note, rate = readSQLrecord(query)
            amount_rub = round(-amount * rate, 2) if rate else 0
            xlsxWriteRow(sheet, row, {
                0: (note, xlsx.formats.Text(row)),
                1: (-amount, xlsx.formats.Number(row, 2)),
                2: (datetime.fromtimestamp(payment_date).strftime('%d.%m.%Y'), xlsx.formats.Text(row)),
                3: (rate, xlsx.formats.Number(row, 4)),
                4: (amount_rub, xlsx.formats.Number(row, 2))
            })
            row += 1
        xlsx.add_totals_footer(sheet, start_row, row, [3, 4])

#-----------------------------------------------------------------------------------------------------------------------
    def prepare_corporate_actions(self, xlsx, account_id, begin, end):
        sheet = self.current_sheet

        # get list of all deals that were opened with corp.action and closed by normal trade
        query = executeSQL(self.db,
                           "SELECT d.open_sid AS o_sid, s.name AS symbol, d.qty AS qty, "
                           "t.timestamp AS t_date, qt.quote AS t_rate, t.settlement AS s_date, qts.quote AS s_rate, "
                           "t.price AS price, t.fee AS fee "
                           "FROM deals AS d "
                           "JOIN sequence AS os ON os.id=d.open_sid AND os.type = 5 "
                           "JOIN sequence AS cs ON cs.id=d.close_sid AND cs.type = 3 "
                           "LEFT JOIN trades AS t ON cs.operation_id=t.id "
                           "LEFT JOIN assets AS s ON t.asset_id=s.id "
                           "LEFT JOIN accounts AS a ON a.id = :account_id "
                           "LEFT JOIN t_last_dates AS ldt ON t.timestamp=ldt.ref_id "
                           "LEFT JOIN quotes AS qt ON ldt.timestamp=qt.timestamp AND a.currency_id=qt.asset_id "
                           "LEFT JOIN t_last_dates AS ldts ON t.settlement=ldts.ref_id "
                           "LEFT JOIN quotes AS qts ON ldts.timestamp=qts.timestamp AND a.currency_id=qts.asset_id "
                           "WHERE t.timestamp>=:begin AND t.timestamp<:end AND d.account_id=:account_id "
                           "ORDER BY t.timestamp",
                           [(":begin", begin), (":end", end), (":account_id", account_id)])

        row = 9
        even_odd = 1
        while query.next():
            sid, symbol, qty, t_date, t_rate, s_date, s_rate, price, fee_usd = readSQLrecord(query)
            amount_usd = round(price * qty, 2)
            amount_rub = round(amount_usd * s_rate, 2) if s_rate else 0
            fee_rub = round(fee_usd * t_rate, 2) if s_rate else 0

            xlsxWriteRow(sheet, row, {
                0: ("Продажа", xlsx.formats.Text(even_odd)),
                1: (datetime.fromtimestamp(t_date).strftime('%d.%m.%Y'), xlsx.formats.Text(even_odd)),
                2: (symbol, xlsx.formats.Text(even_odd)),
                3: (qty, xlsx.formats.Number(even_odd, 4)),
                4: (t_rate, xlsx.formats.Number(even_odd, 4)),
                5: (datetime.fromtimestamp(s_date).strftime('%d.%m.%Y'), xlsx.formats.Text(even_odd)),
                6: (s_rate, xlsx.formats.Number(even_odd, 4)),
                7: (price, xlsx.formats.Number(even_odd, 6)),
                8: (amount_usd, xlsx.formats.Number(even_odd, 2)),
                9: (amount_rub, xlsx.formats.Number(even_odd, 2)),
                10: (fee_usd, xlsx.formats.Number(even_odd, 6)),
                11: (fee_rub, xlsx.formats.Number(even_odd, 2))
            })
            row = row + 1

            row, qty = self.proceed_corporate_action(sid, qty, 1, sheet, xlsx.formats, row, even_odd)

            even_odd = even_odd + 1
            row = row + 1

    def proceed_corporate_action(self, sid, qty, level, sheet, formats, row, even_odd):
        row, qty = self.output_corp_action(sid, qty, level, sheet, formats, row, even_odd)
        row, qty = self.next_corporate_action(sid, qty, level + 1, sheet, formats, row, even_odd)
        return row, qty

    def next_corporate_action(self, sid, qty, level, sheet, formats, row, even_odd):
        # get list of deals that were closed as result of current corporate action
        open_query = executeSQL(self.db, "SELECT d.open_sid AS open_sid, os.type AS op_type "
                                         "FROM deals AS d "
                                         "JOIN sequence AS os ON os.id=d.open_sid AND (os.type = 3 OR os.type = 5) "
                                         "WHERE d.close_sid = :sid "
                                         "ORDER BY d.open_sid",
                                [(":sid", sid)])
        while open_query.next():
            open_sid, op_type = readSQLrecord(open_query)

            if op_type == TransactionType.Trade:
                row, qty = self.output_purchase(open_sid, qty, level, sheet, formats, row, even_odd)
            elif op_type == TransactionType.CorporateAction:
                row, qty = self.proceed_corporate_action(open_sid, qty, level, sheet, formats, row, even_odd)
            else:
                assert False
        return row, qty

    def output_purchase(self, sid, proceed_qty, level, sheet, formats, row, even_odd):
        if proceed_qty <= 0:
            return row, proceed_qty

        indent = ' ' * level * 3
        symbol, deal_qty, t_date, t_rate, s_date, s_rate, price, fee = \
            readSQL(self.db,
                    "SELECT s.name AS symbol, d.qty AS qty, t.timestamp AS t_date, qt.quote AS t_rate, "
                    "t.settlement AS s_date, qts.quote AS s_rate, t.price AS price, t.fee AS fee "
                    "FROM sequence AS os "
                    "JOIN deals AS d ON os.id=d.open_sid AND os.type = 3 "
                    "LEFT JOIN trades AS t ON os.operation_id=t.id "
                    "LEFT JOIN assets AS s ON t.asset_id=s.id "
                    "LEFT JOIN accounts AS a ON a.id = t.account_id "
                    "LEFT JOIN t_last_dates AS ldt ON t.timestamp=ldt.ref_id "
                    "LEFT JOIN quotes AS qt ON ldt.timestamp=qt.timestamp AND a.currency_id=qt.asset_id "
                    "LEFT JOIN t_last_dates AS ldts ON t.settlement=ldts.ref_id "
                    "LEFT JOIN quotes AS qts ON ldts.timestamp=qts.timestamp AND a.currency_id=qts.asset_id "
                    "WHERE os.id = :sid",
                    [(":sid", sid)])

        qty = proceed_qty if proceed_qty < deal_qty else deal_qty
        amount_usd = round(price * qty, 2)
        amount_rub = round(amount_usd * s_rate, 2) if s_rate else 0
        fee_usd = fee * qty / deal_qty
        fee_rub = round(fee_usd * t_rate, 2) if s_rate else 0

        xlsxWriteRow(sheet, row, {
            0: (indent + "Покупка", formats.Text(even_odd)),
            1: (datetime.fromtimestamp(t_date).strftime('%d.%m.%Y'), formats.Text(even_odd)),
            2: (symbol, formats.Text(even_odd)),
            3: (qty, formats.Number(even_odd, 4)),
            4: (t_rate, formats.Number(even_odd, 4)),
            5: (datetime.fromtimestamp(s_date).strftime('%d.%m.%Y'), formats.Text(even_odd)),
            6: (s_rate, formats.Number(even_odd, 4)),
            7: (price, formats.Number(even_odd, 6)),
            8: (amount_usd, formats.Number(even_odd, 2)),
            9: (amount_rub, formats.Number(even_odd, 2)),
            10: (fee_usd, formats.Number(even_odd, 6)),
            11: (fee_rub, formats.Number(even_odd, 2))
        })
        return row + 1, proceed_qty - qty

    def output_corp_action(self, sid, proceed_qty, level, sheet, formats, row, even_odd):
        if proceed_qty <= 0:
            return row, proceed_qty

        indent = ' ' * level * 3
        a_date, type, symbol, qty, symbol_new, qty_new, note = \
            readSQL(self.db, "SELECT a.timestamp AS a_date, a.type, s1.name AS symbol, a.qty AS qty, "
                             "s2.name AS symbol_new, a.qty_new AS qty_new, a.note AS note "
                             "FROM sequence AS os "
                             "JOIN deals AS d ON os.id=d.open_sid AND os.type = 5 "
                             "LEFT JOIN corp_actions AS a ON os.operation_id=a.id "
                             "LEFT JOIN assets AS s1 ON a.asset_id=s1.id "
                             "LEFT JOIN assets AS s2 ON a.asset_id_new=s2.id "
                             "WHERE os.id = :open_sid ",
                    [(":open_sid", sid)])

        qty_before = qty * proceed_qty/qty_new
        qty_after = proceed_qty
        description = self.CorpActionText[type].format(old=symbol, new=symbol_new, before=qty_before, after=qty_after)

        xlsxWriteRow(sheet, row, {
            0: (indent + "Корп. действие", formats.Text(even_odd)),
            1: (datetime.fromtimestamp(a_date).strftime('%d.%m.%Y'), formats.Text(even_odd)),
            2: (description, formats.Text(even_odd), 0, 9, 0)
        })
        row = row + 1
        return row, qty_before
#-----------------------------------------------------------------------------------------------------------------------
