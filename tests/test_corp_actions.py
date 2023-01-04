from decimal import Decimal
from tests.fixtures import project_root, data_path, prepare_db, prepare_db_fifo
from jal.db.ledger import Ledger
from jal.db.db import JalDB
from jal.db.account import JalAccount
from jal.db.operations import CorporateAction
from tests.helpers import create_stocks, create_quotes, create_trades, create_corporate_actions


def test_spin_off(prepare_db_fifo):
    # Prepare trades and corporate actions setup
    test_assets = [
        (4, 'A', 'A SHARE'),
        (5, 'B', 'B SHARE')
    ]
    create_stocks(test_assets, currency_id=2)

    test_corp_actions = [
        (1622548800, CorporateAction.SpinOff, 4, 100.0, 'Spin-off 5 B from 100 A', [(4, 100.0, 1.0), (5, 5.0, 0.0)]),   # 01/06/2021
        (1627819200, CorporateAction.Split, 4, 104.0, 'Split A 104 -> 13', [(4, 13.0, 1.0)])           # 01/08/2021
    ]
    create_corporate_actions(1, test_corp_actions)

    test_trades = [
        (1619870400, 1619870400, 4, 100.0, 14.0, 0.0),   # Buy 100 A x 14.00 01/05/2021
        (1625140800, 1625140800, 4, 4.0, 13.0, 0.0),     # Buy   4 A x 13.00 01/07/2021
        (1629047520, 1629047520, 4, -13.0, 150.0, 0.0)   # Sell 13 A x 150.00 15/08/2021
    ]
    create_trades(1, test_trades)

    create_quotes(2, 2, [(1614600000, 70.0)])
    create_quotes(4, 2, [(1617278400, 15.0)])
    create_quotes(5, 2, [(1617278400, 2.0)])
    create_quotes(4, 2, [(1628683200, 100.0)])

    # Build ledger
    ledger = Ledger()
    ledger.rebuild(from_timestamp=0)

    # Check ledger amounts before selling
    assert JalDB.readSQL("SELECT * FROM ledger WHERE asset_id=4 AND timestamp<1628615520 ORDER BY id DESC LIMIT 1") == [11, 1627819200, 5, 2, 4, 4, 1, '13', '1452', '13', '1452', '', '', '']
    assert JalDB.readSQL("SELECT * FROM ledger WHERE asset_id=5 AND timestamp<1628615520 ORDER BY id DESC LIMIT 1") == [7, 1622548800, 5, 1, 4, 5, 1, '5', '0', '5', '0', '', '', '']
    assert JalDB.readSQL("SELECT * FROM ledger WHERE book_account=3 AND timestamp<1628615520 ORDER BY id DESC LIMIT 1") == [8, 1625140800, 3, 2, 3, 2, 1, '-52', '0', '8548', '0', '', '', '']
    trades = [x for x in JalAccount(1).closed_trades_list() if x.close_operation().timestamp()>=1629047520]
    assert len(trades) == 1
    assert trades[0].profit() == Decimal('497.9999999999999999999999999')


def test_symbol_change(prepare_db_fifo):
    # Prepare trades and corporate actions setup
    test_assets = [
        (4, 'A', 'A SHARE'),
        (5, 'B', 'B SHARE')
    ]
    create_stocks(test_assets, currency_id=2)

    test_corp_actions = [
        (1622548800, CorporateAction.SymbolChange, 4, 100.0, 'Symbol change 100 A -> 100 B', [(5, 100.0, 1.0)])
    ]
    create_corporate_actions(1, test_corp_actions)

    test_trades = [
        (1619870400, 1619870400, 4, 100.0, 10.0, 0.0),      # Buy  100 A x 10.00 01/05/2021
        (1625140800, 1625140800, 5, -100.0, 20.0, 0.0)      # Sell 100 B x 20.00 01/07/2021
    ]
    create_trades(1, test_trades)

    # Build ledgerye
    ledger = Ledger()
    ledger.rebuild(from_timestamp=0)

    trades = JalAccount(1).closed_trades_list()
    assert trades[0].dump() == ['A', 1619870400, 1622548800, Decimal('1E+1'), Decimal('1E+1'), Decimal('1E+2'), Decimal('0'), Decimal('0'), Decimal('0')]
    assert trades[1].dump() == ['B', 1622548800, 1625140800, Decimal('1E+1'), Decimal('2E+1'), Decimal('1E+2'), Decimal('0'), Decimal('1000'), Decimal('100')]


def test_delisting(prepare_db_fifo):
    create_stocks([(4, 'A', 'A SHARE')], currency_id=2)

    test_corp_actions = [
        (1622548800, CorporateAction.Delisting, 4, 100.0, 'Delisting 100 A', [])
    ]
    create_corporate_actions(1, test_corp_actions)

    test_trades = [
        (1619870400, 1619870400, 4, 100.0, 10.0, 0.0)      # Buy  100 A x 10.00 01/05/2021
    ]
    create_trades(1, test_trades)

    # Build ledger
    ledger = Ledger()
    ledger.rebuild(from_timestamp=0)

    trades = JalAccount(1).closed_trades_list()
    assert len(trades) == 1
    assert trades[0].dump() == ['A', 1619870400, 1622548800, Decimal('1E+1'), Decimal('1E+1'), Decimal('1E+2'), Decimal('0'), Decimal('0'), Decimal('0')]

    assert JalDB.readSQL("SELECT * FROM ledger_totals WHERE asset_id=4 ORDER BY id DESC LIMIT 1") == [5, 5, 1, 1622548800, 4, 4, 1, '0', '0']
    assert JalDB.readSQL("SELECT * FROM ledger WHERE book_account=1") == [6, 1622548800, 5, 1, 1, 2, 1, '1E+3', '0', '1E+3', '0', 1, 9, '']
