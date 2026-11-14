import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service_sequences.server import make_server
from service_sequences.service import Workbench
from service_sequences.storage import fingerprint


class TypologyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workbench = Workbench(ROOT, self.temporary.name)
        self.revision = self.workbench.default_revision
        self.trained = self.workbench.fit_typology(
            {"revision": self.revision, "params": {"bootstraps": 10}})
        self.model_id = self.trained["model_id"]

    def test_training_is_idempotent_and_tied_to_dataset(self):
        again = self.workbench.fit_typology(
            {"revision": self.revision, "params": {"bootstraps": 10}})
        self.assertEqual(again["model_id"], self.model_id)
        self.assertEqual(len(self.workbench.list_models()), 1)
        self.assertEqual(again["model"]["training"]["dataset_fingerprint"],
                         fingerprint(self.workbench.store.dataset(self.revision)))

    def test_naming_appends_versions_and_never_overwrites(self):
        first = self.workbench.name_typology(
            {"model_id": self.model_id, "names": {"0": "循环甲"}, "note": "初名"})
        self.assertEqual(first["naming"]["version"], 1)
        self.workbench.name_typology(
            {"model_id": self.model_id, "names": {"0": "稳定循环", "1": "错位起步"}, "note": "复核改名"})
        history = self.workbench.naming_history(self.model_id)["history"]
        self.assertEqual([row["version"] for row in history], [1, 2])
        self.assertEqual(history[0]["names"], {"0": "循环甲"})
        model = self.workbench.get_model(self.model_id)
        self.assertEqual(model["naming"]["version"], 2)
        self.assertEqual(model["naming"]["names"]["0"], "稳定循环")

    def test_blank_or_oversized_name_rejected(self):
        with self.assertRaises(ValueError):
            self.workbench.name_typology(
                {"model_id": self.model_id, "names": {"0": "  "}, "note": ""})
        with self.assertRaises(ValueError):
            self.workbench.name_typology(
                {"model_id": self.model_id, "names": {"0": "x" * 41}, "note": ""})

    def test_new_dataset_training_does_not_rearrange_old_names(self):
        self.workbench.name_typology(
            {"model_id": self.model_id, "names": {"0": "旧类名", "1": "旧类名二"}, "note": "v1"})
        data = self.workbench.store.dataset(self.revision)
        new_data = deepcopy(data)
        new_data["households"].append(deepcopy(new_data["households"][0]))
        new_data["households"][-1]["household"] = "H998"
        new_data["coverage"].append(deepcopy(new_data["coverage"][0]))
        new_data["coverage"][-1].update({"span": "H998-a", "household": "H998"})
        new_data["contacts"].append({
            "contact": "H998-0", "household": "H998", "day": 100, "service": "meal",
            "duration": 30, "reported_at": 101, "source_key": "H998-0"})
        upload = self.workbench.upload({"label": "二期资料", "data": new_data})
        second = self.workbench.fit_typology(
            {"revision": upload["revision"], "params": {"bootstraps": 10}})
        self.assertNotEqual(second["model_id"], self.model_id)
        self.assertEqual(second["model"]["naming"],
                         {"version": 0, "names": {}, "note": "", "created_at": None})
        old = self.workbench.get_model(self.model_id)
        self.assertEqual(old["naming"]["names"]["0"], "旧类名")

    def test_assign_uses_published_model_and_allows_rejection(self):
        outcome = self.workbench.assign_household(
            {"model_id": self.model_id, "household": "H018"})
        self.assertEqual(outcome["naming_version"], 0)
        self.assertIn(outcome["result"]["status"], {"assigned", "rejected"})
        self.assertIsNotNone(outcome["result"]["nearest_cluster_id"])

    def test_assign_unknown_household_raises_lookup(self):
        with self.assertRaises(LookupError):
            self.workbench.assign_household(
                {"model_id": self.model_id, "household": "NOPE"})

    def test_assign_validates_incoming_data_and_versions_it(self):
        with self.assertRaises(ValueError):
            self.workbench.assign_household(
                {"model_id": self.model_id, "data": {}})
        datasets_before = len(self.workbench.store.datasets())
        data = deepcopy(self.workbench.store.dataset(self.revision))
        outcome = self.workbench.assign_household(
            {"model_id": self.model_id, "data": data, "label": "待归类", "household": "H000"})
        self.assertEqual(len(self.workbench.store.datasets()), datasets_before)
        self.assertEqual(outcome["data_revision"], self.revision)

    def test_models_and_naming_survive_restart(self):
        self.workbench.name_typology(
            {"model_id": self.model_id, "names": {"0": "持久名称"}, "note": "重启核对"})
        restarted = Workbench(ROOT, self.temporary.name)
        model = restarted.get_model(self.model_id)
        self.assertEqual(model["naming"]["names"]["0"], "持久名称")
        outcome = restarted.assign_household(
            {"model_id": self.model_id, "household": "H000"})
        self.assertEqual(outcome["result"]["household"], "H000")

    def test_unknown_model_id_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.workbench.get_model("missing-model")
        with self.assertRaises(LookupError):
            self.workbench.assign_household({"model_id": "missing-model"})

    def test_stability_report_exposes_review_evidence(self):
        report = self.workbench.model_stability_report(self.model_id)
        self.assertEqual(report["k_selection"]["recommended_k"],
                         self.trained["model"]["training"]["k_selection"]["recommended_k"])
        self.assertIn("window_sensitivity", report)
        self.assertIn("reject_policy", report)


class TypologyHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.server = make_server(ROOT, self.temporary.name, port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary.cleanup()

    def _post(self, path, payload):
        request = Request(self.base + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            return response.status, json.load(response)

    def test_train_name_and_assign_over_http(self):
        status, trained = self._post("/api/models/train", {"params": {"bootstraps": 10}})
        self.assertEqual(status, 201)
        model_id = trained["model_id"]
        with urlopen(self.base + "/api/models") as response:
            listing = json.load(response)
        self.assertEqual([row["model_id"] for row in listing], [model_id])
        status, naming = self._post("/api/models/name", {
            "model_id": model_id, "names": {"0": "页面命名"}, "note": "HTTP"})
        self.assertEqual(status, 201)
        self.assertEqual(naming["naming"]["version"], 1)
        with urlopen(self.base + "/api/models/" + model_id) as response:
            model = json.load(response)
        self.assertEqual(model["naming"]["names"]["0"], "页面命名")
        status, outcome = self._post("/api/models/assign", {
            "model_id": model_id, "household": "H000"})
        self.assertEqual(status, 201)
        self.assertEqual(outcome["result"]["household"], "H000")
        with urlopen(self.base + "/api/models/" + model_id + "/stability") as response:
            report = json.load(response)
        self.assertIn("k_selection", report)
        with urlopen(self.base + "/api/models/" + model_id + "/names") as response:
            history = json.load(response)
        self.assertEqual(len(history["history"]), 1)

    def test_unknown_model_returns_404(self):
        request = Request(self.base + "/api/models/nope")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_bad_train_params_return_400(self):
        request = Request(self.base + "/api/models/train",
                          data=json.dumps({"params": {"k": 99}}).encode(),
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_existing_endpoints_keep_their_contract(self):
        with urlopen(self.base + "/health") as response:
            self.assertEqual(json.load(response)["status"], "ok")
        request = Request(self.base + "/api/notebooks",
                          data=json.dumps({"title": "旧流程仍可用"}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            self.assertEqual(response.status, 201)


if __name__ == "__main__":
    unittest.main()
