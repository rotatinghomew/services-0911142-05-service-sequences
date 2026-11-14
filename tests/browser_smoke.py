from pathlib import Path
import importlib
import json
import sys
import tempfile
import threading
import unittest

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class BrowserWorkflow(unittest.TestCase):
    def test_scope_freeze_raw_and_export(self):
        product = json.loads((ROOT / "product.json").read_text())
        package = json.loads((ROOT / "package.json").read_text())
        module = package["scripts"]["start"].split()[2]
        server_module = importlib.import_module(module + ".server")
        with tempfile.TemporaryDirectory() as state:
            server = server_module.make_server(ROOT, state, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as runtime:
                    browser = runtime.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto("http://127.0.0.1:" + str(server.server_port), wait_until="networkidle")
                        page.locator("#status").filter(has_text="已更新描述").wait_for()
                        self.assertEqual(page.locator("h1").inner_text(), product["name"])
                        self.assertGreater(page.locator("#rows tr").count(), 0)
                        page.locator("#rows tr").first.click()
                        self.assertIn("{", page.locator("#details").inner_text())
                        page.locator("#raw").click()
                        raw_table = next(iter(json.loads((ROOT / "fixtures/data.json").read_text())))
                        page.wait_for_function("key => document.getElementById('details').textContent.includes(JSON.stringify(key))", arg=raw_table)
                        page.locator("#title").fill("复核记录")
                        page.locator("#save").click()
                        page.locator("#status").filter(has_text="已保存记录").wait_for()
                        frozen = json.loads(page.locator("#details").inner_text())
                        option = product["options"][-1]
                        alternative = next(value for value in option[2] if value != product["selection"][option[0]])
                        page.locator("#select-" + option[0]).select_option(alternative)
                        page.locator("#inspect").click()
                        page.wait_for_function("value => {try{return JSON.parse(document.getElementById('details').textContent).selection[value.key] === value.choice}catch{return false}}", arg={"key":option[0],"choice":alternative})
                        page.locator("#archive button").first.click()
                        page.locator("#status").filter(has_text="已读取保存的描述").wait_for()
                        self.assertEqual(frozen, json.loads(page.locator("#details").inner_text()))
                        with page.expect_download() as event:
                            page.locator("#export").click()
                        exported = json.loads(Path(event.value.path()).read_text())
                        self.assertEqual(exported["description"]["revision"], frozen["revision"])
                        self.assertIn(raw_table, exported["data"])
                        page.locator("#upload").set_input_files(str(ROOT / "fixtures/data.json"))
                        page.locator("#status").filter(has_text="已导入独立数据版本").wait_for()
                        self.assertEqual(page.locator("#revision option").count(), 1)
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join()
                server.server_close()

    def test_typology_train_name_assign_and_stability(self):
        package = json.loads((ROOT / "package.json").read_text())
        module = package["scripts"]["start"].split()[2]
        server_module = importlib.import_module(module + ".server")
        with tempfile.TemporaryDirectory() as state:
            server = server_module.make_server(ROOT, state, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as runtime:
                    browser = runtime.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto("http://127.0.0.1:" + str(server.server_port), wait_until="networkidle")
                        page.locator("#train-bootstraps").fill("10")
                        page.locator("#train").click()
                        page.locator("#typology-status").filter(has_text="人工命名版本 v0").wait_for(timeout=120000)
                        self.assertGreaterEqual(page.locator(".cluster-card").count(), 2)
                        self.assertGreater(page.locator(".mini-table tr.recommended").count(), 0)
                        # 人工命名：保存为命名版本 v1。
                        first_name_input = page.locator("input[data-cluster='0']")
                        first_name_input.fill("稳定循环接访组")
                        page.locator("#naming-note").fill("浏览器复核命名")
                        page.locator("#save-names").click()
                        page.locator("#typology-status").filter(has_text="人工命名版本 v1").wait_for()
                        self.assertIn("稳定循环接访组", page.locator(".cluster-card h3").first.inner_text())
                        # 稳定性与窗长检验报告。
                        page.locator("#show-stability").click()
                        page.wait_for_function(
                            "() => document.getElementById('details').textContent.includes('window_sensitivity')")
                        report = json.loads(page.locator("#details").inner_text())
                        self.assertIn("k_selection", report)
                        self.assertTrue(report["window_sensitivity"]["available"])
                        # 命名历史。
                        page.locator("#show-names").click()
                        page.wait_for_function(
                            "() => document.getElementById('details').textContent.includes('浏览器复核命名')")
                        # 接访归类：训练资料中的户应能放入已发布分型或被明确拒识。
                        page.locator("#assign-household").fill("H000")
                        page.locator("#assign").click()
                        page.locator("#assign-result .assign-card").wait_for()
                        card_text = page.locator("#assign-result .assign-card").first.inner_text()
                        self.assertTrue("不适合归类" in card_text or "类 0" in card_text or "类 1" in card_text)
                        self.assertIn("为什么接近", card_text)
                        # 上传一份含全新短历程户的资料，验证拒识路径在页面可见。
                        fixture = json.loads((ROOT / "fixtures" / "data.json").read_text())
                        new_data = json.loads(json.dumps(fixture))
                        new_household = dict(new_data["households"][0])
                        new_household.update({"household": "HNEW", "cohort": "late",
                                              "registered_day": 90, "prior_history": False,
                                              "end_day": 180})
                        new_data["households"].append(new_household)
                        new_coverage = dict(new_data["coverage"][0])
                        new_coverage.update({"span": "HNEW-a", "household": "HNEW",
                                             "start": 90, "stop": 180, "status": "complete",
                                             "gap_start": None, "gap_stop": None})
                        new_data["coverage"].append(new_coverage)
                        new_data["contacts"].append({
                            "contact": "HNEW-0", "household": "HNEW", "day": 91,
                            "service": "meal", "duration": 30, "reported_at": 92,
                            "source_key": "HNEW-0"})
                        incoming = Path(state) / "incoming.json"
                        incoming.write_text(json.dumps(new_data, ensure_ascii=False), encoding="utf-8")
                        page.locator("#assign-file").set_input_files(str(incoming))
                        page.locator("#assign-household").fill("HNEW")
                        page.locator("#assign").click()
                        page.locator("#assign-result .assign-card").wait_for()
                        new_card = page.locator("#assign-result .assign-card").first.inner_text()
                        self.assertIn("HNEW", new_card)
                        self.assertTrue("不适合归类" in new_card or "为什么接近" in new_card)
                        # 旧命名版本不随归类与新资料改变。
                        self.assertIn("人工命名版本 v1", page.locator("#typology-status").inner_text())
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
