"""Monthly card fee estimates.

Every cost uses the same formula:
  debit takings x debit rate
  + credit takings x credit rate
  + (transactions x auth fee + monthly fee), with VAT added to those fixed fees

Percentage fees are VAT-exempt in the UK; authorisation and rental fees are not.
Partner rates are loaded server-side and only their cheapest total ever leaves
this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent

MIN_VOLUME, MAX_VOLUME = 100, 250_000
MIN_ATV, MAX_ATV = 1, 2_000


@dataclass(frozen=True)
class Rates:
    debit: float          # decimal, 0.01 = 1%
    credit: float
    auth: float           # pounds per transaction, before VAT
    monthly: float        # pounds per month, before VAT
    vat_on_fixed: bool = True


def monthly_cost(volume: float, atv: float, rates: Rates, debit_share: float, vat_rate: float) -> float:
    debit_volume = volume * debit_share
    credit_volume = volume - debit_volume
    transactions = volume / atv
    fixed = transactions * rates.auth + rates.monthly
    if rates.vat_on_fixed:
        fixed *= 1 + vat_rate
    return debit_volume * rates.debit + credit_volume * rates.credit + fixed


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class Estimator:
    def __init__(self, partners_path: Path, competitors_path: Path):
        cfg = _load(partners_path)
        self.vat_rate = float(cfg["vat_rate"])
        self.debit_share = float(cfg["debit_share"])
        self._partners = [
            Rates(p["debit"], p["credit"], p["auth"], p["monthly"], True) for p in cfg["partners"]
        ]
        comp = _load(competitors_path)
        self.checked_on = comp["checked_on"]
        self._competitors = comp["providers"]

    def _cost(self, volume, atv, rates):
        return monthly_cost(volume, atv, rates, self.debit_share, self.vat_rate)

    def ours(self, volume: float, atv: float) -> float:
        return min(self._cost(volume, atv, r) for r in self._partners)

    def competitors(self, volume: float, atv: float) -> list[dict]:
        """Cheapest published plan per provider that applies at this volume."""
        out = []
        annual = volume * 12
        for provider in self._competitors:
            best = None
            for plan in provider["plans"]:
                cap = plan.get("max_annual_volume")
                if cap and annual > cap:
                    continue
                rates = Rates(plan["debit"], plan["credit"], plan["auth"], plan["monthly"], plan["vat_on_fixed"])
                cost = self._cost(volume, atv, rates)
                if best is None or cost < best["monthly"]:
                    best = {
                        "name": provider["name"],
                        "plan": plan["plan"],
                        "monthly": cost,
                        "note": plan.get("note", ""),
                        "source": provider["source"],
                    }
            if best:
                out.append(best)
        return out

    def estimate(self, volume: float, atv: float, current: Rates | None = None) -> dict:
        volume = clamp(volume, MIN_VOLUME, MAX_VOLUME)
        atv = clamp(atv, MIN_ATV, MAX_ATV)
        # Whole pounds only: exact pence would let anyone solve for the confidential rates.
        ours = float(round(self.ours(volume, atv)))
        rivals = self.competitors(volume, atv)

        rows = [{"kind": "ours", "name": "Card Machines Direct", "plan": "Cheapest available option", "monthly": ours}]
        rows += [{"kind": "competitor", **r} for r in rivals]

        if current is not None:
            current_cost = self._cost(volume, atv, current)
            rows.append({"kind": "current", "name": "Your current provider", "plan": "Using the fees you entered", "monthly": current_cost})
            basis, compare_to = "current", current_cost
            compare_label = "Your current provider"
        else:
            basis = "published"
            priciest = max(rivals, key=lambda r: r["monthly"], default=None)
            compare_to = priciest["monthly"] if priciest else ours
            compare_label = f'{priciest["name"]}, {priciest["plan"]}' if priciest else ""

        for r in rows:
            r["monthly"] = round(r["monthly"], 2)
            if r["kind"] != "ours":  # our per-£100 figure would reveal a flat confidential rate directly
                r["per_100"] = round(r["monthly"] / volume * 100, 2)
        rows.sort(key=lambda r: r["monthly"])

        saving = round(compare_to - ours, 2)
        return {
            "volume": volume,
            "atv": atv,
            "transactions": round(volume / atv),
            "debit_share": self.debit_share,
            "basis": basis,
            "compare_label": compare_label,
            "compare_monthly": round(compare_to, 2),
            "ours_monthly": round(ours, 2),
            "saving_monthly": max(saving, 0.0),
            "we_are_cheaper": saving > 0.005,
            "rows": rows,
            "rates_checked_on": self.checked_on,
        }


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def parse_current(form: dict) -> Rates | None:
    """Current-provider fees as typed by the customer: % values, pence, pounds.

    Returns None unless at least a debit or credit rate was given.
    """
    def num(key):
        raw = form.get(key)
        if raw in (None, ""):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    debit, credit = num("debit_pct"), num("credit_pct")
    if debit is None and credit is None:
        return None
    debit = debit if debit is not None else credit
    credit = credit if credit is not None else debit
    if debit > 15 or credit > 15:
        return None
    auth_p = min(num("auth_p") or 0.0, 100.0)
    monthly = min(num("monthly") or num("monthly_fee") or 0.0, 1000.0)  # calculator sends "monthly", quote form "monthly_fee"
    return Rates(debit / 100, credit / 100, auth_p / 100, monthly, True)
