import copy
import importlib.util
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from algotrading.training import _fit, split_samples
from algotrading.walk_forward import (
    fold_for_date,
    load_registry,
    predict_member,
    score,
    validate_registry,
)


class WalkForwardTests(unittest.TestCase):
    def test_supplied_registry_has_old_and_modern_purged_folds(self):
        registry = load_registry()
        validate_registry(registry)
        self.assertGreaterEqual(len(registry["folds"]), 30)
        for year in (2000, 2001, 2008, 2009, 2020, 2022, 2025):
            fold = fold_for_date(date(year, 8, 1))
            self.assertEqual(len(fold["members"]), 3)
            for member in fold["members"]:
                self.assertLess(member["last_validation_label_date"], f"{year}-01-01")
                self.assertLess(member["last_training_label_date"], member["validation_start"])

    def test_no_future_model_fallback(self):
        self.assertIsNone(fold_for_date(date(1900, 1, 1)))
        self.assertIsNone(score("nn_stock_pattern_v1", [0] * 7, date(1900, 1, 1)))
        with patch(
            "algotrading.walk_forward.load_registry",
            side_effect=AssertionError("must use pinned artifact"),
        ):
            self.assertIsNone(score("nn_stock_pattern_v1", [0] * 7, date(2024, 1, 1), {}))

    def test_boundary_leakage_fails_closed(self):
        registry = copy.deepcopy(load_registry())
        fold = registry["folds"][0]
        fold["members"][0]["last_validation_label_date"] = fold["effective_from"]
        with self.assertRaisesRegex(ValueError, "label leakage"):
            validate_registry(registry)

    def test_fold_selection_crosses_year_not_trained_future(self):
        earlier = fold_for_date(date(2008, 12, 31))
        later = fold_for_date(date(2009, 1, 1))
        self.assertEqual(earlier["test_year"], 2008)
        self.assertEqual(later["test_year"], 2009)

    @unittest.skipUnless(importlib.util.find_spec("torch"), "optional PyTorch training dependency")
    def test_exported_inference_matches_pytorch(self):
        import torch

        features = [0.2, -0.3, 0.1, 0.4, -0.2, 0.7, 0.15]
        for year in (1996, 2008, 2020, 2026):
            member = fold_for_date(date(year, 1, 1))["members"][0]
            x = (
                (torch.tensor(features) - torch.tensor(member["mean"]))
                / torch.tensor(member["scale"])
            ).clamp(-5, 5)
            hidden = torch.tanh(
                torch.nn.functional.linear(
                    x, torch.tensor(member["hidden_weights"]), torch.tensor(member["hidden_bias"])
                )
            )
            expected = (
                torch.nn.functional.linear(
                    hidden,
                    torch.tensor(member["output_weights"]),
                    torch.tensor(member["output_bias"]),
                )
                / 10
            ).clamp(-0.5, 0.5)
            for actual, target in zip(predict_member(member, features), expected.tolist()):
                self.assertAlmostEqual(actual, target, places=6)

    def test_cross_sectional_rows_stay_together_and_overlaps_purged(self):
        samples = [
            {"date": d, "label_end": d + timedelta(days=30), "symbol": s}
            for d in (
                date(2006, 6, 1),
                date(2006, 12, 20),
                date(2007, 6, 1),
                date(2007, 12, 20),
                date(2008, 6, 1),
            )
            for s in ("A", "B")
        ]
        train, validation, test = split_samples(samples, 2008)
        self.assertEqual(len(train), 2)
        self.assertEqual(len(validation), 2)
        self.assertEqual(len(test), 2)
        self.assertTrue(all(s["label_end"] < date(2007, 1, 1) for s in train))
        self.assertTrue(all(s["label_end"] < date(2008, 1, 1) for s in validation))

    @unittest.skipUnless(importlib.util.find_spec("torch"), "optional PyTorch training dependency")
    def test_blinded_test_perturbation_cannot_change_fitted_weights(self):
        samples = [
            {
                "date": date(y, 1, 1) + timedelta(days=i * 5),
                "label_end": date(y, 1, 1) + timedelta(days=i * 5 + 21),
                "features": [i / 20] * 7,
                "target": [i / 100, i / 200],
            }
            for y in (2006, 2007, 2008)
            for i in range(20)
        ]
        train, validation, _ = split_samples(samples, 2008)
        first = _fit(train, validation, 42, 2)
        for sample in samples:
            if sample["date"].year == 2008:
                sample["target"] = [999, -999]
                sample["features"] = [999] * 7
        train, validation, _ = split_samples(samples, 2008)
        second = _fit(train, validation, 42, 2)
        self.assertEqual(first, second)
        prediction = predict_member(first, [0] * 7)
        self.assertTrue(all(-0.5 <= p <= 0.5 for p in prediction))
