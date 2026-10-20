"""Saves the ICICI card's figures into the shared transactions database.

The card page gives a summary, not a statement: what is due, what is
spent, and the limits. That maps onto the same `card_cycles` row every
HDFC card uses, so the widget shows an ICICI card beside them with no
special case — the account id carries the bank.

Transactions are not written here. This profile has no linked bank
account and the card had none this cycle; when it does, they go through
store.ingest.upsert_rows like every other bank's.
"""
from __future__ import annotations

from . import store


def save_card(card: dict) -> str:
    """`card` is what probe.card_details() read off the page. Returns the
    account id it wrote."""
    last4 = card.get("last4")
    if not last4:
        raise ValueError("the card's last four digits are missing; nothing was saved")

    conn = store.conn()
    account_id = store.ingest.ensure_account(conn, "ICICI", "credit", last4, network="RUPAY")
    if card.get("cardName"):
        with store.db.write(conn):
            conn.execute("UPDATE accounts SET display_name = ? WHERE id = ? AND display_name IS NULL",
                         (card["cardName"], account_id))

    # The same shape set_card_cycle takes from HDFC. ICICI prints a credit
    # as "+ ₹40 C.R.", which card_details() has already turned into -₹40,
    # and unbilled purchases as the spend for the cycle.
    store.ingest.set_card_cycle(conn, account_id, {
        "name": card.get("cardName", ""),
        "total_outstanding": card.get("totalDue", ""),
        "upcoming_payment_due": card.get("totalDue", ""),
        "unbilled_amount": card.get("spentThisCycle", "") if not _is_credit(card) else "",
        "payment_status": "NO DUES" if _zero(card.get("totalDue")) else "AMOUNT DUE",
        "credit_limit_total": card.get("totalLimit", ""),
        "available_limit": card.get("availableLimit", ""),
        "last_statement_amount": card.get("totalDue", ""),
    })
    return account_id


def _zero(amount: str | None) -> bool:
    return not amount or amount.replace("₹", "").replace(",", "").strip() in ("0", "0.00")


def _is_credit(card: dict) -> bool:
    return str(card.get("spentThisCycle", "")).startswith("-")
