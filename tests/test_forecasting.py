"""PDMA forecasting: exact risk bands, honest summaries (no zero-filling), permissions, layers and the C02 panel.
Network sources are not called here; stored rows stand in for a completed refresh."""
import unittest
from datetime import date, datetime, timedelta

from controller.forecasting import category
from model.forecast_model import DistrictRiskAssessment, ForecastDistrict, ForecastRun, HazardForecast
from tests.test_donations import NGO, DonationFixture

TODAY = (datetime.utcnow() + timedelta(hours=5)).date()


class BandTests(unittest.TestCase):
    def test_bands_are_exact_and_mutually_exclusive(self):
        cases = {0: "low", 59.9: "low", 60: "watch", 79.9: "watch", 80: "warning", 89.9: "warning", 90: "immediate",
                 100: "immediate", None: "insufficient", float("nan"): "insufficient"}
        for score, expected in cases.items():
            self.assertEqual(category(score), expected, score)


class ForecastApiTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo = cls.login(*NGO)
        now = datetime.utcnow()
        with cls.Session() as db:
            run = ForecastRun(kind="model", status="success", trigger="manual", started_at=now, finished_at=now, records=4)
            db.add(run)
            db.add(ForecastRun(kind="open_meteo", status="success", trigger="manual", started_at=now, finished_at=now))
            for pcode, name, prov, score in (("PK601", "Dadu", "Sindh", 91.0), ("PK602", "Thatta", "Sindh", 62.5),
                                             ("PK401", "Swat", "Khyber Pakhtunkhwa", 12.0),
                                             ("PK402", "Buner", "Khyber Pakhtunkhwa", None)):
                db.add(ForecastDistrict(pcode=pcode, name=name, province=prov, lat=30.0, lon=70.0, area_km2=100.0,
                                        flood_mode="slow", hist_floods_train=1, gwt_q99=0.8))
                db.flush()
                db.add(DistrictRiskAssessment(
                    district_pcode=pcode, as_of_date=TODAY - timedelta(days=2) if score is not None else None,
                    probability=score / 100 if score is not None else None, risk_score=score,
                    risk_category=category(score), model_version="rf_plain@test", data_completeness=1.0 if score else 0.4,
                    primary_indicator="Deep soil saturation" if score else None, calculated_at=now, run_id=None,
                    calculation_details={"inputs": {"rain_7d": 40.0}} if score else {"missing": ["NASA POWER observations"]}))
            for i in range(7):
                db.add(HazardForecast(district_pcode="PK601", variable="precipitation_sum", value=10.0,
                                      unit="mm", source="Open-Meteo forecast", forecast_date=TODAY + timedelta(days=i),
                                      generated_at=now, retrieved_at=now))
                db.add(HazardForecast(district_pcode="PK601", variable="temperature_2m_max", value=46.0 if i == 2 else 30.0,
                                      unit="°C", source="Open-Meteo forecast", forecast_date=TODAY + timedelta(days=i),
                                      generated_at=now, retrieved_at=now))
            db.commit()

    def get(self, path, headers=None):
        return self.client.get(f"/api/forecast/{path}", headers=headers or self.admin)

    def test_01_summary_counts_and_no_zero_filling(self):
        s = self.get("summary").json()
        self.assertEqual(s["total_districts"], 4)
        self.assertEqual(s["counts"], {"low": 1, "watch": 1, "warning": 0, "immediate": 1, "insufficient": 1})
        self.assertEqual(s["at_risk"], 2)  # watch + warning + immediate
        self.assertEqual(s["districts_with_valid_data"], 3)
        self.assertEqual(s["overall_risk_index"], round((91.0 + 62.5 + 12.0) / 3, 1))  # insufficient excluded
        self.assertEqual(s["highest_risk"]["district"], "Dadu")
        self.assertEqual(self.get("summary?province=Sindh").json()["total_districts"], 2)

    def test_02_table_sorted_and_filtered(self):
        t = self.get("districts").json()
        self.assertEqual([r["district"] for r in t["items"]], ["Dadu", "Thatta", "Swat", "Buner"])
        self.assertEqual(t["items"][0]["action"], "Urgent assessment and response required")
        self.assertEqual(t["items"][-1]["risk_label"], "Insufficient Data")
        self.assertEqual([r["district"] for r in self.get("districts?category=watch").json()["items"]], ["Thatta"])
        self.assertEqual([r["district"] for r in self.get("districts?hazard=heat").json()["items"]], ["Dadu"])

    def test_03_detail_uses_stored_forecast(self):
        d = self.get("districts/PK601").json()
        self.assertEqual(len(d["timeline"]), 7)
        self.assertEqual(d["weather"]["forecast_rain_3d_mm"], 30.0)
        self.assertEqual(d["weather"]["forecast_rain_7d_mm"], 70.0)
        self.assertTrue(d["extreme_heat"])
        self.assertEqual(self.get("districts/NOPE").status_code, 404)
        self.assertIn("missing", self.get("districts/PK402").json()["calculation"])

    def test_04_staff_only(self):
        for h in (self.donor, self.ngo, self.dc):
            self.assertEqual(self.get("summary", h).status_code, 403)
        self.assertEqual(self.client.post("/api/forecast/refresh", headers=self.donor).status_code, 403)

    def test_05_layers_and_geo(self):
        layers = {x["key"]: x for x in self.get("layers").json()}
        self.assertTrue(layers["risk"]["available"])
        self.assertFalse(layers["flood_nowcast"]["available"])
        self.assertEqual(self.get("layers/risk").json()["districts"]["PK601"]["category"], "immediate")
        self.assertEqual(self.get("layers/nonsense").status_code, 422)
        geo = self.get("geo/districts")
        self.assertEqual(geo.status_code, 200)
        self.assertEqual(len(geo.json()["features"]), 160)

    def test_06_model_performance_reports_real_c02_result(self):
        p = self.get("model-performance").json()
        self.assertTrue(p["available"])
        self.assertEqual(p["evaluation_split"]["ratio"], "99.0:1")
        self.assertGreaterEqual(p["pr_auc"], 0.70)
        self.assertEqual(p["status"], "PASS")
        cm = p["confusion_matrix"]
        self.assertAlmostEqual(p["recall"], cm["TP"] / (cm["TP"] + cm["FN"]), places=6)
        self.assertEqual(p["majority_class_baseline"]["recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
