from pathlib import Path
import json

from .domain import explore, validate
from .pipeline import classify_journey, train_model, with_dataset
from .storage import Store, fingerprint


class Workbench:
    def __init__(self, root, state):
        self.root = Path(root)
        self.product = json.loads((self.root / "product.json").read_text(encoding="utf-8"))
        self.store = Store(Path(state) / "research.sqlite3")
        initial = json.loads((self.root / "fixtures" / "data.json").read_text(encoding="utf-8"))
        validate(initial)
        self.default_revision = self.store.put_dataset("演示材料", initial)

    def context(self):
        return {"product": self.product, "default_revision": self.default_revision,
                "datasets": self.store.datasets(), "notebooks": self.store.notebooks()}

    def upload(self, body):
        data = body["data"]
        try:
            validate(data)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("字段结构不完整或类型不符") from error
        return {"revision": self.store.put_dataset(body.get("label", ""), data)}

    def inspect(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        result = explore(data, selection)
        return {"revision": revision, "selection": selection, "data_sha256": fingerprint(data), "view": result}

    def save(self, body):
        result = self.inspect(body)
        return self.store.save(body.get("title", ""), result["revision"], result["selection"], result)

    def fit_typology(self, body):
        """在指定不可变数据版本上训练分型；训练参数由服务端校验并固化进模型。"""
        revision = body.get("revision", self.default_revision)
        data = self.store.dataset(revision)
        params = body.get("params")
        if params is not None and not isinstance(params, dict):
            raise ValueError("训练参数必须是对象")
        artifact = train_model(data, params)
        model_id = self.store.put_model(revision, artifact)
        return {"model_id": model_id, "revision": revision,
                "model": self._public_model(self.store.model(model_id))}

    def list_models(self):
        return self.store.models()

    def get_model(self, model_id):
        return self._public_model(self.store.model(model_id))

    def name_typology(self, body):
        """人工命名形成独立的可追溯版本，不改动学习产物本身。"""
        model_id = body.get("model_id", "")
        names = body.get("names", {})
        note = body.get("note", "")
        naming = self.store.name_clusters(model_id, names, note)
        return {"model_id": model_id, "naming": naming}

    def naming_history(self, model_id):
        self.store.model(model_id)
        return {"model_id": model_id, "history": self.store.naming_history(model_id)}

    def assign_household(self, body):
        """把新资料中的户放入已发布分型；资料先完整校验并形成独立数据版本。"""
        model_id = body.get("model_id", "")
        model = self.store.model(model_id)
        training_data = self.store.dataset(self._model_revision(model_id))
        payload = body.get("data")
        household = body.get("household")
        if payload is None:
            data = training_data
            revision = self._model_revision(model_id)
        else:
            validate(payload)
            revision = self.store.put_dataset(body.get("label", "待归类资料"), payload)
            data = payload
        if household is not None and not isinstance(household, str):
            raise ValueError("户标识必须是字符串")
        result = classify_journey(data, with_dataset(model, training_data), household)
        return {"model_id": model_id, "data_revision": revision,
                "naming_version": model["naming"]["version"], "result": result}

    def model_stability_report(self, model_id):
        """汇总复核所需的稳定性证据，页面与复核导出共用同一份口径。"""
        model = self.store.model(model_id)
        return {"model_id": model_id,
                "dataset_fingerprint": model["training"]["dataset_fingerprint"],
                "params": model["training"]["params"],
                "k_selection": model["training"]["k_selection"],
                "window_sensitivity": model["training"]["window_sensitivity"],
                "reject_policy": model["reject_policy"],
                "naming": model["naming"]}

    def _model_revision(self, model_id):
        for row in self.store.models():
            if row["model_id"] == model_id:
                return row["revision"]
        raise LookupError("分型版本不存在")

    def _public_model(self, model):
        """对外视图剥离运行期字段，补入 model_id 与人工名称，保持制品本身不可变。"""
        public = {key: value for key, value in model.items()
                  if not key.startswith("_")}
        public["model_id"] = model["model_fingerprint"]
        return public
