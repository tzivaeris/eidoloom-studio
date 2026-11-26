"""The printed receipt for an invoice."""

from invoice import format_eur, invoice_total


def receipt(lines):
    """One line per item, then net, VAT and the total to pay."""
    totals = invoice_total(lines)
    rows = [f"{qty} x {format_eur(price)}" for price, qty in lines]
    rows.append(f"Net: {format_eur(totals['net'])}")
    rows.append(f"VAT 24%: {format_eur(totals['vat'])}")
    rows.append(f"Total: {format_eur(totals['gross'])}")
    return "\n".join(rows)
