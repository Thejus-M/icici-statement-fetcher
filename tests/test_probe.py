"""Tests for the ICICI capture run.

The card wording below is a trimmed copy of what the live page printed,
with the amounts changed — the real capture holds account data and never
goes into the repo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.probe import card_details  # noqa: E402

# The nav runs straight into the card name on this page, which is why the
# card-name match has to drop the all-caps words in front of it.
CARD_TEXT = (
    "Ask iPal Call Us 1800 1080 View Demo THEJUS OVERVIEW ACCOUNTS PAYMENT & TRANSFER "
    "DEPOSITS CARDS LOANS INVESTMENTS INSURANCE CUSTOMER SERVICE Coral RuPay -1234 ₹ 0 "
    "Min amount due ₹0 Pay Now TEST USER NAME 6528 **** **** 1234 EXP **** CVV *** SET PIN "
    "STATEMENTS PDF XLS DOWNLOAD CURRENT PAST EMI STATUS Credits Received You have no "
    "transactions in this billing cycle. CARD ANALYSIS Total spent + ₹40 C.R. "
    "Total amount due ₹0 Available credit limit ₹1,30,040 MODIFY YOUR CARD LIMIT "
    "₹1,30,000 TOTAL CARD LIMIT MANAGE YOUR CREDIT CARD"
)


def test_card_details_reads_every_figure_the_page_prints():
    card = card_details(CARD_TEXT)
    assert card["cardName"] == "Coral RuPay"      # not "CUSTOMER SERVICE Coral RuPay"
    assert card["last4"] == "1234"
    assert card["holder"] == "TEST USER NAME"
    assert card["number"] == "6528 **** **** 1234"
    assert card["totalDue"] == "₹0"
    assert card["minimumDue"] == "₹0"
    assert card["availableLimit"] == "₹1,30,040"
    assert card["totalLimit"] == "₹1,30,000"
    assert card["transactionsThisCycle"] == "none"


def test_a_credit_balance_is_not_read_as_spend():
    """"Total spent + ₹40 C.R." is money the bank owes back — it is why the
    available limit is larger than the total limit."""
    assert card_details(CARD_TEXT)["spentThisCycle"] == "-₹40"
    spent = CARD_TEXT.replace("Total spent + ₹40 C.R.", "Total spent ₹2,500.00")
    assert card_details(spent)["spentThisCycle"] == "₹2,500.00"


def test_a_field_the_page_stops_printing_goes_missing_rather_than_wrong():
    """A wording change should show up as a gap in the printout, not as a
    number carried over from somewhere else."""
    without = CARD_TEXT.replace("Available credit limit ₹1,30,040", "")
    card = card_details(without)
    assert "availableLimit" not in card
    assert card["totalLimit"] == "₹1,30,000"


def test_nothing_is_invented_from_an_empty_page():
    assert card_details("") == {}
