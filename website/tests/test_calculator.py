import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator import Estimator, Rates, monthly_cost, parse_current  # noqa: E402


class FormulaTest(unittest.TestCase):
    def test_brief_worked_example(self):
        # £10,000/month at £20 average, 1% debit, 2% credit, 1p auth, £10/month -> £128
        rates = Rates(0.01, 0.02, 0.01, 10.0, True)
        self.assertAlmostEqual(monthly_cost(10_000, 20, rates, 0.9, 0.2), 128.0, places=6)

    def test_no_vat_when_not_flagged(self):
        rates = Rates(0.01, 0.01, 0.0, 10.0, False)
        self.assertAlmostEqual(monthly_cost(1_000, 20, rates, 0.9, 0.2), 20.0)

    def test_parse_current(self):
        r = parse_current({"debit_pct": "1", "credit_pct": "2", "auth_p": "1", "monthly": "10"})
        self.assertEqual(r, Rates(0.01, 0.02, 0.01, 10.0, True))
        self.assertIsNone(parse_current({"monthly": "10"}))
        self.assertIsNone(parse_current({"debit_pct": "abc"}))


class EstimatorTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "p.json").write_text(json.dumps({
            "vat_rate": 0.2, "debit_share": 0.9,
            "partners": [
                {"id": "X", "debit": 0.01, "credit": 0.02, "auth": 0.01, "monthly": 10},
                {"id": "Y", "debit": 0.02, "credit": 0.02, "auth": 0, "monthly": 0},
            ],
        }))
        (tmp / "c.json").write_text(json.dumps({
            "checked_on": "2026-10-02",
            "providers": [
                {"name": "Flat", "source": "https://example.com",
                 "plans": [{"plan": "PAYG", "debit": 0.0175, "credit": 0.0175, "auth": 0, "monthly": 0, "vat_on_fixed": False}]},
                {"name": "Capped", "source": "https://example.com",
                 "plans": [{"plan": "Small", "debit": 0.001, "credit": 0.001, "auth": 0, "monthly": 0,
                            "vat_on_fixed": False, "max_annual_volume": 50_000}]},
            ],
        }))
        self.est = Estimator(tmp / "p.json", tmp / "c.json")

    def test_cheapest_partner_and_saving(self):
        out = self.est.estimate(10_000, 20)
        ours = next(r for r in out["rows"] if r["kind"] == "ours")
        self.assertEqual(ours["monthly"], 128.0)
        self.assertEqual(out["saving_monthly"], 175.0 - 128.0)
        # Capped plan does not apply at £120k a year
        self.assertNotIn("Capped", [r["name"] for r in out["rows"]])

    def test_no_partner_detail_leaks(self):
        out = self.est.estimate(10_000, 20)
        blob = json.dumps(out)
        self.assertNotIn('"id"', blob)
        self.assertNotIn("debit\": 0.01", blob)

    def test_current_supplier_becomes_basis(self):
        out = self.est.estimate(10_000, 20, Rates(0.005, 0.005, 0, 0, True))
        self.assertEqual(out["basis"], "current")
        self.assertFalse(out["we_are_cheaper"])
        self.assertEqual(out["saving_monthly"], 0.0)

    def test_quote_form_monthly_fee_key(self):
        # The quote form posts "monthly_fee"; the homepage calculator posts "monthly". Both must count.
        for key in ("monthly", "monthly_fee"):
            cur = parse_current({"debit_pct": "1", "credit_pct": "2", "auth_p": "1", key: "10"})
            out = self.est.estimate(10_000, 20, cur)
            current = next(r for r in out["rows"] if r["kind"] == "current")
            self.assertEqual(current["monthly"], 128.0)


if __name__ == "__main__":
    unittest.main()
