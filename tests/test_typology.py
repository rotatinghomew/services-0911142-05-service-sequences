import json
import math
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_sequences.clustering import (
    DEFAULT_WEIGHTS, JourneyDistance, adjusted_rand_index, align_labels,
    coassignment_rate, edit_distance, fit_standardizer, pairwise_matrix, pam_swap,
    silhouette_score,
)
from service_sequences.features import (
    STRUCTURE_ORDER, extract_journey, extract_journeys, truncate_journeys,
)
from service_sequences.pipeline import (
    MIN_STABLE_ARI, DeterministicSampler, classify_journey, train_model, with_dataset,
)

FIXTURE = json.loads((ROOT / "fixtures" / "data.json").read_text(encoding="utf-8"))


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.data = deepcopy(FIXTURE)

    def test_duplicate_reports_are_counted_not_deleted_from_raw(self):
        before = len(self.data["contacts"])
        journeys = extract_journeys(self.data)
        duplicated = [j for j in journeys if j["duplicate_count"] > 0]
        self.assertTrue(duplicated)
        target = next(j for j in duplicated if j["household"] == "H000")
        # H000 有一条同 source_key 重复上报：原始 4 条，去重后 3 个事件。
        self.assertEqual(target["raw_contact_count"], 4)
        self.assertEqual(target["duplicate_count"], 1)
        self.assertEqual(len(target["events"]), 3)
        self.assertEqual(len(self.data["contacts"]), before)
        self.assertGreater(target["structure"]["duplicate_ratio"], 0)

    def test_uncovered_gap_differs_from_covered_silence(self):
        partial = next(row for row in self.data["coverage"] if row["status"] == "partial")
        household = next(h for h in self.data["households"]
                         if h["household"] == partial["household"])
        contacts = [row for row in self.data["contacts"]
                    if row["household"] == household["household"]]
        journey = extract_journey(household, contacts, [partial])
        self.assertEqual(journey["uncovered_days"],
                         partial["gap_stop"] - partial["gap_start"])
        self.assertGreater(journey["structure"]["uncovered_ratio"], 0)
        # 覆盖内无服务由 leading/trailing silence 表达，不会并入未观测缺口。
        self.assertIn("leading_silence_ratio", STRUCTURE_ORDER)
        self.assertIn("trailing_silence_ratio", STRUCTURE_ORDER)

    def test_prior_history_and_registration_are_not_reset_to_first_contact(self):
        household = next(h for h in self.data["households"] if h["prior_history"])
        contacts = [row for row in self.data["contacts"]
                    if row["household"] == household["household"]]
        coverage = [row for row in self.data["coverage"]
                    if row["household"] == household["household"]]
        journey = extract_journey(household, contacts, coverage)
        self.assertEqual(journey["structure"]["prior_history"], 1.0)
        self.assertEqual(journey["window_start"], coverage[0]["start"])

    def test_zero_contact_household_is_still_a_journey(self):
        household = deepcopy(self.data["households"][0])
        household["household"] = "EMPTY"
        coverage = deepcopy(self.data["coverage"][0])
        coverage["span"] = "EMPTY-a"
        coverage["household"] = "EMPTY"
        journey = extract_journey(household, [], [coverage])
        self.assertEqual(journey["events"], [])
        self.assertEqual(journey["service_sequence"], [])
        self.assertEqual(journey["structure"]["log_events"], 0.0)
        # 零接触时整窗既是前导沉默也是尾随沉默，两个比值各自为 1，不互相分摊。
        self.assertEqual(journey["structure"]["leading_silence_ratio"], 1.0)
        self.assertEqual(journey["structure"]["trailing_silence_ratio"], 1.0)

    def test_window_normalization_makes_cohorts_comparable(self):
        journeys = {j["household"]: j for j in extract_journeys(self.data)}
        late = next(j for j in journeys.values() if j["cohort"] == "late")
        early = next(j for j in journeys.values() if j["cohort"] == "early")
        self.assertLess(late["window_length"], early["window_length"])
        for key in ("gap_mean", "gap_max_ratio", "active_span_ratio"):
            self.assertLessEqual(journeys["H000"]["structure"][key], 1.0 + 1e-9)
        self.assertIsInstance(late["structure"][key], float)

    def test_truncation_only_cuts_contacts_and_keeps_coverage(self):
        cutoff = 105
        truncated = truncate_journeys(self.data, cutoff)
        self.assertTrue(all(event["day"] <= cutoff for j in truncated for event in j["events"]))
        self.assertEqual(len(self.data["coverage"]), len(FIXTURE["coverage"]))


class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self.journeys = extract_journeys(deepcopy(FIXTURE))
        self.standardizer = fit_standardizer(self.journeys)

    def test_distance_components_are_bounded_and_symmetric(self):
        matrix = pairwise_matrix(self.journeys, self.standardizer)
        calculator = JourneyDistance(self.journeys, self.standardizer)
        n = len(self.journeys)
        for i in range(0, n, 25):
            for j in range(i + 1, n, 25):
                parts = calculator.components(i, j)
                # 序列与节奏分量天然落在 [0,1]；结构分量是稳健标准化后的欧氏距离，无上界。
                self.assertTrue(0.0 <= parts["sequence"] <= 1.0)
                self.assertTrue(0.0 <= parts["cadence"] <= 1.0)
                self.assertTrue(parts["structure"] >= 0.0)
                self.assertAlmostEqual(matrix[i][j], matrix[j][i])
        self.assertEqual(matrix[3][3], 0.0)

    def test_edit_distance_basic_cases(self):
        self.assertEqual(edit_distance([], []), 0)
        self.assertEqual(edit_distance(["a"], ["a"]), 0)
        self.assertEqual(edit_distance(["a"], ["b"]), 1)
        self.assertEqual(edit_distance(["a", "b", "c"], ["a", "x", "c"]), 1)
        self.assertEqual(edit_distance(["a", "b"], ["a", "b", "c"]), 1)

    def test_pam_is_deterministic_and_medoids_are_real_members(self):
        matrix = pairwise_matrix(self.journeys, self.standardizer)
        medoids_a, labels_a = pam_swap(matrix, 3)
        medoids_b, labels_b = pam_swap(matrix, 3)
        self.assertEqual(medoids_a, medoids_b)
        self.assertEqual(labels_a, labels_b)
        for medoid in medoids_a:
            self.assertIn(labels_a[medoid], range(3))
            self.assertEqual(labels_a[medoid], medoids_a.index(medoid))
        self.assertEqual(sorted(set(labels_a)), [0, 1, 2])

    def test_ari_and_coassignment_known_values(self):
        self.assertEqual(adjusted_rand_index([0, 0, 1, 1], [0, 0, 1, 1]), 1.0)
        self.assertEqual(adjusted_rand_index([0, 0, 0, 0], [0, 1, 0, 1]), 0.0)
        self.assertEqual(coassignment_rate([0, 0, 1], [1, 1, 0]), 1.0)

    def test_align_labels_remaps_without_dropping_households(self):
        reference = [0, 0, 1, 1, 2]
        candidate = [2, 2, 0, 0, 1]
        aligned = align_labels(reference, candidate)
        self.assertEqual(aligned, reference)
        self.assertEqual(sorted(set(aligned)), [0, 1, 2])

    def test_silhouette_needs_at_least_two_clusters(self):
        matrix = pairwise_matrix(self.journeys[:8], self.standardizer)
        self.assertIsNone(silhouette_score(matrix, [0] * 8))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.data = deepcopy(FIXTURE)

    def test_training_is_deterministic_and_content_addressed(self):
        first = train_model(self.data, {"bootstraps": 12})
        second = train_model(deepcopy(self.data), {"bootstraps": 12})
        self.assertEqual(first["model_fingerprint"], second["model_fingerprint"])
        self.assertEqual(first["clusters"], second["clusters"])
        self.assertEqual(first["assignment"], second["assignment"])

    def test_different_training_params_change_the_model(self):
        baseline = train_model(self.data, {"bootstraps": 12})
        forced = train_model(self.data, {"bootstraps": 12, "k": 3})
        self.assertNotEqual(baseline["model_fingerprint"], forced["model_fingerprint"])
        self.assertEqual(len(forced["clusters"]), 3)

    def test_k_selection_reports_explainable_criterion_and_stability(self):
        model = train_model(self.data, {"bootstraps": 12})
        selection = model["training"]["k_selection"]
        self.assertTrue(selection["candidates"])
        self.assertTrue(all(2 <= row["k"] <= 8 for row in selection["candidates"]))
        for row in selection["candidates"]:
            self.assertIsNotNone(row["silhouette"])
            self.assertGreaterEqual(row["bootstrap_ari_min"], -1.0001)
        recommended = next(row for row in selection["candidates"]
                           if row["k"] == selection["recommended_k"])
        self.assertTrue(recommended["stable"])
        self.assertGreaterEqual(recommended["bootstrap_ari_mean"], MIN_STABLE_ARI)

    def test_window_sensitivity_separates_window_length_from_path_change(self):
        model = train_model(self.data, {"bootstraps": 12})
        sensitivity = model["training"]["window_sensitivity"]
        self.assertTrue(sensitivity["available"])
        self.assertEqual(sensitivity["cutoff_days"], 105)
        self.assertGreaterEqual(sensitivity["ari_all_households"], 0.0)
        self.assertIn("interpretation", sensitivity)
        for moved in sensitivity["moved_households"]:
            self.assertIn("household", moved)
            self.assertNotEqual(moved["published_cluster"], moved["truncated_cluster"])

    def test_every_household_gets_an_assignment_and_clusters_cover_all(self):
        model = train_model(self.data, {"bootstraps": 12})
        assigned_households = {row["household"] for row in model["assignment"]}
        self.assertEqual(assigned_households, {h["household"] for h in self.data["households"]})
        member_total = sum(cluster["size"] for cluster in model["clusters"])
        self.assertEqual(member_total, len(self.data["households"]))
        for cluster in model["clusters"]:
            self.assertTrue(cluster["representative_path"]["service_sequence"])
            self.assertIn("within_group_variation", cluster)

    def test_classification_supports_rejection_and_explains_neighbors(self):
        model = with_dataset(train_model(self.data, {"bootstraps": 12}), self.data)
        results = classify_journey(self.data, model)
        self.assertEqual(len(results), len(self.data["households"]))
        rejected = [row for row in results if row["status"] == "rejected"]
        for row in rejected:
            self.assertIsNone(row["cluster_id"])
            self.assertTrue(row["reject_reasons"])
            self.assertIsNotNone(row["nearest_cluster_id"])
        accepted = next(row for row in results if row["status"] == "assigned")
        self.assertIsNotNone(accepted["cluster_id"])
        self.assertTrue(accepted["similar_households"])
        for neighbor in accepted["similar_households"]:
            self.assertEqual(set(neighbor["components"]),
                             {"sequence", "cadence", "structure"})

    def test_short_new_journey_can_be_refused_rather_than_forced_into_a_type(self):
        model = with_dataset(train_model(self.data, {"bootstraps": 12}), self.data)
        new_data = deepcopy(self.data)
        new_data["households"].append({
            "household": "HSHORT", "cohort": "late", "channel": "home",
            "registered_day": 90, "prior_history": False, "end_day": 180})
        new_data["coverage"].append({
            "span": "HSHORT-a", "household": "HSHORT", "start": 90, "stop": 180,
            "status": "complete", "gap_start": None, "gap_stop": None})
        new_data["contacts"].append({
            "contact": "HSHORT-0", "household": "HSHORT", "day": 91, "service": "meal",
            "duration": 30, "reported_at": 92, "source_key": "HSHORT-0"})
        result = classify_journey(new_data, model, household="HSHORT")
        self.assertEqual(result["household"], "HSHORT")
        self.assertIsNotNone(result["nearest_cluster_id"])
        self.assertTrue(
            result["status"] == "rejected"
            or (result["status"] == "assigned" and result["similar_households"]))

    def test_sampler_is_deterministic(self):
        left = DeterministicSampler(7)
        right = DeterministicSampler(7)
        self.assertEqual([left.integer(100) for _ in range(20)],
                         [right.integer(100) for _ in range(20)])

    def test_too_few_households_refused(self):
        tiny = {key: value[:4] if key != "contacts" else
                [row for row in value if row["household"] in {h["household"] for h in value[:4]}]
                for key, value in deepcopy(self.data).items()}
        tiny["coverage"] = [row for row in self.data["coverage"]
                            if row["household"] in {h["household"] for h in tiny["households"]}]
        with self.assertRaises(ValueError):
            train_model(tiny, {"bootstraps": 5})


if __name__ == "__main__":
    unittest.main()
