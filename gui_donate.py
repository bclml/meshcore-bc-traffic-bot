"""Support mcIRC: an optional donation through PayPal - one time in $1 CAD units (the donor chooses how many, 1-100), or a $1 CAD monthly subscription.

mcIRC only opens PayPal's own web page in the browser; the payment is made there.  mcIRC never sees or stores card or account details."""
import tkinter as tk
import webbrowser
from tkinter import ttk

import gui_platform
from gui_common import BG

UNITS_URL = "https://www.paypal.com/ncp/payment/TVFX47SD92LSE"      # PayPal payment link: $1.00 CAD each, the donor picks the quantity (up to 100)
SUBSCRIBE_URL = "https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=C2AYGMYMTKVZJ&currency_code=CAD"     # PayPal "Subscribe" button: $1.00 CAD every month
CURRENCY = "CAD"

TEXT = ("mcIRC is free and stays free. Every feature works exactly the same whether or not you donate - "
        "donating is NOT mandatory in any way.\n\nIf mcIRC is useful to you, a donation is greatly appreciated and helps keep the project going. Thank you!")


def subscribe_url():
    """The $1 CAD monthly subscription (PayPal shows the terms and lets the donor cancel any time)."""
    return SUBSCRIBE_URL


def units_url():
    """$1 CAD each; PayPal lets the donor choose how many (1-100)."""
    return UNITS_URL


class DonateDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, bg=BG)
        self.title("Support mcIRC")
        self.transient(app.root)
        self.resizable(False, False)
        tk.Label(self, text="Support mcIRC  ♥", bg=BG, font=(gui_platform.DIALOG_FONT_NAME, 12, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(self, text=TEXT, bg=BG, justify="left", anchor="w", wraplength=420).pack(fill="x", padx=14, pady=(0, 10))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=14)
        ttk.Button(row, text=f"Donate - ${1} {CURRENCY} each, you choose how many (1 to 100)", command=lambda: self.go(units_url())).pack(fill="x", pady=2)
        if SUBSCRIBE_URL:
            ttk.Button(row, text=f"Give ${1} {CURRENCY} every month (cancel any time)", command=lambda: self.go(subscribe_url())).pack(fill="x", pady=2)
        note = ("Each button opens PayPal in your web browser, where you review everything before paying. The first is a one-time donation; the monthly subscription can be "
                "stopped at any time in PayPal (Settings > Payments > Manage automatic payments). mcIRC never sees your card or account details.")
        if not SUBSCRIBE_URL: note = "This opens PayPal in your web browser, where you review everything before paying. It is a one-time donation. mcIRC never sees your card or account details."
        tk.Label(self, text=note, bg=BG, fg="#555", justify="left", anchor="w",
                 wraplength=420).pack(fill="x", padx=14, pady=(10, 4))
        ttk.Button(self, text="No thanks / Close", command=self.destroy).pack(anchor="e", padx=14, pady=(4, 12))

    def go(self, url):
        webbrowser.open(url)
        self.destroy()
