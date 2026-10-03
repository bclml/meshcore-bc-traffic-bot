"""Support mcIRC: an optional donation through PayPal.Me (one time, any amount).  A monthly option appears only if SUBSCRIBE_URL is set.

mcIRC only opens PayPal's own web page in the browser; the payment is made there.  mcIRC never sees or stores card or account details."""
import tkinter as tk
import webbrowser
from tkinter import ttk

import gui_platform
from gui_common import BG

PAYPAL_ME = "https://www.paypal.me/7787266445"    # the project's PayPal.Me page (PayPal shows the name "MCL Services")
SUBSCRIBE_URL = ""      # a PayPal *subscription* link ($1 CAD / month); the monthly button stays hidden while this is empty. (A "payment link" is only a one-time payment.)
CURRENCY = "CAD"

TEXT = ("mcIRC is free and stays free. Every feature works exactly the same whether or not you donate - "
        "donating is NOT mandatory in any way.\n\nIf mcIRC is useful to you, a donation is greatly appreciated and helps keep the project going. Thank you!")


def one_time_url():
    """The PayPal.Me page: you type whatever amount you like."""
    return PAYPAL_ME


def monthly_url():
    return SUBSCRIBE_URL


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
        ttk.Button(row, text="Give once - you choose the amount", command=lambda: self.go(one_time_url())).pack(fill="x", pady=2)
        if SUBSCRIBE_URL:
            ttk.Button(row, text=f"Give $1 {CURRENCY} every month (cancel any time)", command=lambda: self.go(monthly_url())).pack(fill="x", pady=2)
        note = "This opens PayPal.Me in your web browser, where you choose the amount and review everything before paying"
        if SUBSCRIBE_URL: note += ". The monthly option can be stopped any time in PayPal (Settings > Payments > Manage automatic payments)"
        tk.Label(self, text=note + ". mcIRC never sees your card or account details.", bg=BG, fg="#555", justify="left", anchor="w",
                 wraplength=420).pack(fill="x", padx=14, pady=(10, 4))
        ttk.Button(self, text="No thanks / Close", command=self.destroy).pack(anchor="e", padx=14, pady=(4, 12))

    def go(self, url):
        webbrowser.open(url)
        self.destroy()
