"""Unit tests: amounts, dates, table/line parsing, merchant cleanup, categorization."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.processing.amounts import find_amounts, parse_amount
from app.processing.categorize import categorize, clean_merchant
from app.processing.dates import DateContext, detect_order, parse_date
from app.processing.parsing import parse_line, parse_lines, parse_table
from app.processing.types import PageContent


@pytest.mark.parametrize(
    ("text", "value", "currency", "credit"),
    [
        ("1,234.56", "1234.56", None, None),
        ("1.234,56", "1234.56", None, None),
        ("QAR 1,500.00", "1500.00", "QAR", None),
        ("1,500.00 QAR", "1500.00", "QAR", None),
        ("$45.99", "45.99", "USD", None),
        ("€420,00", "420.00", "EUR", None),
        ("(123.45)", "123.45", None, True),
        ("-99.00", "99.00", None, True),
        ("5,000.00 CR", "5000.00", None, True),
        ("250.00 DR", "250.00", None, False),
        ("12.500 KWD", "12.500", "KWD", None),
        ("1 234,50", "1234.50", None, None),
    ],
)
def test_parse_amount_formats(text: str, value: str, currency: str | None, credit: bool | None) -> None:
    parsed = parse_amount(text)
    assert parsed.value == Decimal(value)
    assert parsed.currency == currency
    assert parsed.is_credit == credit


def test_parse_amount_rejects_garbage_and_flags_missing() -> None:
    assert parse_amount("abc").value is None
    assert "malformed_amount" in parse_amount("abc").reasons
    assert parse_amount("").reasons == ["missing_amount"]
    assert parse_amount(None).value is None


def test_three_decimal_amount_in_two_decimal_currency_is_uncertain() -> None:
    parsed = parse_amount("12.345", "QAR")
    assert parsed.uncertain and "unusual_decimal_places" in parsed.reasons


def test_dollar_sign_is_marked_ambiguous() -> None:
    assert parse_amount("$10.00").currency_ambiguous is True
    assert parse_amount("USD 10.00").currency_ambiguous is False


def test_find_amounts_ignores_dates_and_card_numbers() -> None:
    line = "15/09/2025 STARBUCKS 4821 DOHA 31.00"
    found = [a[2].value for a in find_amounts(line)]
    assert found == [Decimal("31.00")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-09-15", date(2025, 9, 15)),
        ("15/09/2025", date(2025, 9, 15)),
        ("15-Sep-2025", date(2025, 9, 15)),
        ("15 Sep 2025", date(2025, 9, 15)),
        ("Sep 15, 2025", date(2025, 9, 15)),
        ("15.09.2025", date(2025, 9, 15)),
        ("15 SEP", date(2025, 9, 15)),
        ("15/09", date(2025, 9, 15)),
    ],
)
def test_parse_date_formats(text: str, expected: date) -> None:
    ctx = DateContext(order="DMY", order_confident=True, period_end=date(2025, 9, 30))
    assert parse_date(text, ctx).value == expected


def test_ambiguous_numeric_date_is_flagged_when_order_unknown() -> None:
    ctx = DateContext(order="DMY", order_confident=False, period_end=date(2025, 9, 30))
    parsed = parse_date("03/04/2025", ctx)
    assert parsed.value == date(2025, 4, 3)
    assert parsed.uncertain and "ambiguous_day_month_order" in parsed.reasons


def test_impossible_date_is_rejected_not_invented() -> None:
    ctx = DateContext(order="DMY", order_confident=True)
    parsed = parse_date("31/02/2025", ctx)
    assert parsed.value is None and "impossible_date" in parsed.reasons


def test_year_is_inferred_across_new_year() -> None:
    ctx = DateContext(order="DMY", order_confident=True, period_end=date(2026, 1, 20))
    assert parse_date("28 Dec", ctx).value == date(2025, 12, 28)


def test_detect_order_ignores_amounts() -> None:
    # "9.99" must not count as month-first evidence.
    assert detect_order(["15/09/2025 APPLE USD 9.99 36.76"]) == ("DMY", True)
    assert detect_order(["09/15/2025 purchase"]) == ("MDY", True)


CTX = DateContext(order="DMY", order_confident=True, period_end=date(2025, 9, 30))


def test_parse_table_with_header_and_foreign_column() -> None:
    table = [
        ["Trans Date", "Post Date", "Description", "Foreign Amount", "Amount (QAR)"],
        ["04/09/2025", "05/09/2025", "AMAZON.COM*MK12 SEATTLE US", "USD 89.99", "331.16"],
        ["10/09/2025", "10/09/2025", "PAYMENT RECEIVED - THANK YOU", "", "5,000.00 CR"],
        ["", "", "Total", "", "5,331.16"],
    ]
    rows = parse_table(table, 1, CTX, "docling")
    assert len(rows) == 2
    assert rows[0].currency_hint == "QAR"
    assert rows[0].original_amount_text == "USD 89.99" and rows[0].original_currency == "USD"
    assert rows[1].type_hint == "credit"


def test_parse_table_with_separate_debit_credit_columns() -> None:
    table = [
        ["Date", "Details", "Debit", "Credit"],
        ["01 Sep 2025", "Grocery Store", "120.00", ""],
        ["02 Sep 2025", "Refund Online Shop", "", "45.00"],
    ]
    rows = parse_table(table, 1, CTX, "docling")
    assert [(r.amount_text, r.type_hint) for r in rows] == [("120.00", "debit"), ("45.00", "credit")]


def test_parse_table_without_header_infers_columns() -> None:
    table = [
        ["01/09/2025", "CAFE ONE DOHA", "25.00"],
        ["02/09/2025", "BOOK SHOP", "80.50"],
        ["03/09/2025", "TAXI", "12.00"],
    ]
    rows = parse_table(table, 1, CTX, "docling")
    assert [r.description for r in rows] == ["CAFE ONE DOHA", "BOOK SHOP", "TAXI"]
    assert rows[1].amount_text == "80.50"


def test_parse_line_layouts() -> None:
    t = parse_line("15/09 16/09 NOBU RESTAURANT DOHA QA 845.00", 1, CTX, "QAR", "docling")
    assert t and t.date_text == "15/09" and t.posting_date_text == "16/09"
    assert t.description == "NOBU RESTAURANT DOHA QA" and t.amount_text == "845.00"
    t2 = parse_line("Sep 13, 2025 UBER TRIP LONDON GBP 23.40 112.18", 1, CTX, "QAR", "docling")
    assert t2 and t2.original_currency == "GBP" and t2.amount_text == "112.18"
    t3 = parse_line("Trans Post Description Amount 08/28 08/29 WHOLE FOODS AUSTIN TX 87.43", 1, CTX, "USD", "docling")
    assert t3 and t3.description == "WHOLE FOODS AUSTIN TX" and t3.amount_text == "87.43"
    assert parse_line("Previous Balance 5,432.10", 1, CTX, "QAR", "docling") is None
    assert parse_line("Payment received on 15/09/2025 thank you 100.00", 1, CTX, "QAR", "docling") is None
    assert parse_line("Statement Date 30/09/2025 Total due 1,234.00", 1, CTX, "QAR", "docling") is None


def test_currency_code_between_amounts_belongs_to_the_following_amount() -> None:
    found = [(str(p.value), p.currency) for _, _, p in find_amounts("HOTEL PARIS FR 412.66 EUR 378.50 @ 1.09027", "USD")]
    assert found == [("412.66", "USD"), ("378.50", "EUR")]
    t = parse_line("09/14 09/15 HOTEL LE MARAIS PARIS FR 412.66 EUR 378.50 @ 1.09027", 1,
                   DateContext(order="MDY", order_confident=True, period_end=date(2025, 9, 24)), "USD", "docling")
    assert t and t.amount_text == "412.66" and t.original_currency == "EUR" and t.original_amount_text == "EUR 378.50"


def test_split_merged_rows_from_one_text_block() -> None:
    from app.processing.docling_extractor import split_merged_rows

    block = ("08/28 08/29 WHOLE FOODS MARKET #10234 AUSTIN TX 87.43 08/30 08/31 SHELL OIL 5744 AUSTIN TX 41.20 "
             "09/02 09/02 PAYMENT THANK YOU -1,200.00 Total Purchases $128.63 Total Payments $1,200.00")
    parts = split_merged_rows(block, DateContext(default_year=2025))
    assert parts == [
        "08/28 08/29 WHOLE FOODS MARKET #10234 AUSTIN TX 87.43",
        "08/30 08/31 SHELL OIL 5744 AUSTIN TX 41.20",
        "09/02 09/02 PAYMENT THANK YOU -1,200.00",
        "Total Purchases $128.63",
        "Total Payments $1,200.00",
    ]
    header = "Billing Cycle: 08/25/2025 - 09/24/2025 Previous Balance $1,500.00 New Balance $1,018.95 Minimum Payment Due $35.00"
    assert split_merged_rows(header, DateContext(default_year=2025)) == [
        "Billing Cycle: 08/25/2025 - 09/24/2025 Previous Balance $1,500.00",
        "New Balance $1,018.95",
        "Minimum Payment Due $35.00",
    ]


def test_several_printed_totals_on_one_line() -> None:
    from app.processing.metadata import detect_metadata

    page = PageContent(page_number=1, method="docling", lines=[
        "Amounts in USD", "Previous Balance $1,500.00 New Balance $1,018.95 Minimum Payment Due $35.00",
        "Total Purchases $718.95 Total Payments $1,200.00",
    ])
    meta, _ = detect_metadata([page])
    assert meta.printed_totals == {"previous_balance": "1500.00", "closing_balance": "1018.95", "minimum_due": "35.00",
                                   "total_debits": "718.95", "total_credits": "1200.00"}


def test_parse_lines_attaches_foreign_amount_on_next_line() -> None:
    rows = parse_lines(["14/09/2025 BOOKING.COM AMSTERDAM 1,701.00", "   EUR 420.00"], 1, CTX, "QAR", "docling")
    assert len(rows) == 1 and rows[0].original_currency == "EUR"


def test_page_content_text() -> None:
    assert PageContent(page_number=1, method="docling", lines=["a", "b"]).text == "a\nb"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AMAZON.COM*MK12 SEATTLE US", "Amazon.com"),
        ("STARBUCKS CITY CENTER DOHA QA", "Starbucks City Center"),
        ("LULU HYPERMARKET AL GHARRAFA QA", "Lulu Hypermarket"),
        ("UBER *TRIP LONDON GB", "Uber Trip"),
        ("SPOTIFY STOCKHOLM SE", "Spotify"),
        ("KFC DOHA QA", "KFC"),
        ("POS PURCHASE 1234567 IKEA", "IKEA"),
        ("FOREIGN TRANSACTION FEE", "Foreign Transaction Fee"),
        ("WHOLE FOODS MARKET #10234 AUSTIN TX", "Whole Foods Market"),
        ("SHELL OIL 5744 AUSTIN TX", "Shell Oil"),
    ],
)
def test_clean_merchant(raw: str, expected: str) -> None:
    assert clean_merchant(raw) == expected


def test_clean_merchant_returns_none_when_nothing_usable() -> None:
    assert clean_merchant("123456 **** 99") is None


@pytest.mark.parametrize(
    ("desc", "category"),
    [
        ("NOBU RESTAURANT FOUR SEASONS", "Dining"),
        ("TALABAT FOOD DELIVERY", "Dining"),
        ("CARREFOUR HYPERMARKET", "Groceries"),
        ("QATAR AIRWAYS", "Travel"),
        ("OOREDOO POSTPAID BILL", "Utilities & Telecom"),
        ("FOREIGN TRANSACTION FEE", "Fees & Interest"),
        ("PAYMENT RECEIVED - THANK YOU", "Payments & Credits"),
        ("XYZ UNKNOWN LLC", "Uncategorized"),
    ],
)
def test_categorize(desc: str, category: str) -> None:
    assert categorize(desc, clean_merchant(desc), "debit")[0] == category
