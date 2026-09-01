import copy
import unittest
from datetime import date, timedelta

from algotrading.cli import build_parser
from algotrading.model_validation import audit_models, purge_label_overlap, purged_walk_forward
from algotrading.nn_models import load_model, validate_model


class ModelValidationTests(unittest.TestCase):
    def test_artifact_shape_and_feature_order_fail_closed(self):
        model = copy.deepcopy(load_model("nn_stock_pattern_v1"))
        model["hidden_weights"][0].pop()
        with self.assertRaisesRegex(ValueError, "dimensions"):
            validate_model(model)
        model = copy.deepcopy(load_model("nn_stock_pattern_v1"))
        model["input_features"].reverse()
        with self.assertRaisesRegex(ValueError, "feature order"):
            validate_model(model)

    def test_unverified_provenance_is_reported_honestly(self):
        reports = audit_models()
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["parameter_count"], 55)
        self.assertIn("training_data_sha256", reports[0]["missing_provenance"])
        self.assertEqual(len(reports[0]["sha256"]), 64)

    def test_purged_folds_never_share_future_labels(self):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(1500)]
        folds = purged_walk_forward(dates)
        self.assertGreater(len(folds), 1)
        for fold in folds:
            self.assertLess(max(fold["train"]) + 21, min(fold["validation"]))
            self.assertLess(max(fold["validation"]) + 21, min(fold["test"]))
            self.assertEqual(len(fold["test"]), 126)
            self.assertLess(max(fold["test"]) + 21, len(dates))
        self.assertTrue(set(folds[0]["test"]).isdisjoint(folds[1]["test"]))

    def test_boundary_crossing_targets_are_purged(self):
        samples = [
            {"feature_date": date(2022, 12, 1), "label_end": date(2022, 12, 30)},
            {"feature_date": date(2022, 12, 20), "label_end": date(2023, 1, 20)},
        ]
        self.assertEqual(purge_label_overlap(samples, date(2023, 1, 1)), samples[:1])

    def test_shuffled_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            purged_walk_forward([date(2024, 1, 3), date(2024, 1, 2)])

    def test_incomplete_test_labels_do_not_produce_fold(self):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(20)]
        self.assertEqual(purged_walk_forward(dates, 3, 6, 3, 3), [])
        self.assertEqual(len(purged_walk_forward(dates + [date(2020, 1, 21)], 3, 6, 3, 3)), 1)

    def test_cli_exposes_model_audit(self):
        args = build_parser().parse_args(["model", "audit"])
        self.assertEqual(args.handler.__name__, "_handle_model_audit")
