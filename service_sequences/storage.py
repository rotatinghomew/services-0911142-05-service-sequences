from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3


NAMING_LIMIT = 40
DESCRIPTION_LIMIT = 600


def canonical(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    return value


def encode(value):
    return json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    revision TEXT PRIMARY KEY, label TEXT NOT NULL, body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    revision TEXT NOT NULL REFERENCES datasets(revision),
                    selection TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY,
                    revision TEXT NOT NULL REFERENCES datasets(revision),
                    body TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_names (
                    model_id TEXT NOT NULL REFERENCES models(model_id),
                    version INTEGER NOT NULL,
                    names TEXT NOT NULL, note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (model_id, version)
                );
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def put_dataset(self, label, data):
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("请提供不超过120字的数据名称")
        revision = fingerprint(data)
        with self.connection() as connection:
            connection.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?,?)",
                               (revision, label.strip(), encode(data), datetime.now(timezone.utc).isoformat()))
        return revision

    def datasets(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT revision,label,created_at FROM datasets ORDER BY created_at DESC,revision")]

    def dataset(self, revision):
        with self.connection() as connection:
            row = connection.execute("SELECT body FROM datasets WHERE revision=?", (revision,)).fetchone()
        if row is None:
            raise LookupError("数据版本不存在")
        return json.loads(row["body"])

    def save(self, title, revision, selection, result):
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("分析名称应为1至160字")
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM datasets WHERE revision=?", (revision,)).fetchone() is None:
                raise LookupError("数据版本不存在")
            cursor = connection.execute("INSERT INTO notebooks(title,revision,selection,result,created_at) VALUES(?,?,?,?,?)",
                                        (title.strip(), revision, encode(selection), encode(result), datetime.now(timezone.utc).isoformat()))
            identifier = cursor.lastrowid
        return self.notebook(identifier)

    def notebooks(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT id,title,revision,created_at FROM notebooks ORDER BY id DESC")]

    def notebook(self, identifier):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM notebooks WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise LookupError("分析记录不存在")
        result = dict(row)
        for field in ["selection", "result"]:
            result[field] = json.loads(result[field])
        result["dataset"] = self.dataset(result["revision"])
        return result

    def put_model(self, revision, artifact):
        """内容寻址保存模型制品；同一学习产物重复训练不产生新版本。"""
        model_id = artifact["model_fingerprint"]
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM datasets WHERE revision=?",
                                  (revision,)).fetchone() is None:
                raise LookupError("数据版本不存在")
            connection.execute(
                "INSERT OR IGNORE INTO models VALUES(?,?,?,?)",
                (model_id, revision, encode(artifact),
                 datetime.now(timezone.utc).isoformat()))
        return model_id

    def models(self):
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT model_id,revision,created_at FROM models ORDER BY created_at DESC,model_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def model(self, model_id):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM models WHERE model_id=?",
                                     (model_id,)).fetchone()
        if row is None:
            raise LookupError("分型版本不存在")
        artifact = json.loads(row["body"])
        artifact["naming"] = self.latest_naming(model_id)
        return artifact

    def latest_naming(self, model_id):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT version,names,note,created_at FROM model_names WHERE model_id=? "
                "ORDER BY version DESC LIMIT 1", (model_id,)).fetchone()
        if row is None:
            return {"version": 0, "names": {}, "note": "", "created_at": None}
        return {"version": row["version"], "names": json.loads(row["names"]),
                "note": row["note"], "created_at": row["created_at"]}

    def naming_history(self, model_id):
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT version,names,note,created_at FROM model_names WHERE model_id=? "
                "ORDER BY version", (model_id,)).fetchall()
        return [{"version": row["version"], "names": json.loads(row["names"]),
                 "note": row["note"], "created_at": row["created_at"]} for row in rows]

    def name_clusters(self, model_id, names, note):
        """人工命名只追加新版本，从不改写或删除旧名称；新模型不继承旧模型的名称。"""
        if not isinstance(names, dict) or not names:
            raise ValueError("至少为一个类别提供人工名称")
        for key, value in names.items():
            if not isinstance(value, str) or not value.strip() or len(value) > NAMING_LIMIT:
                raise ValueError("类别名称应为1至%d字" % NAMING_LIMIT)
        if not isinstance(note, str) or len(note) > DESCRIPTION_LIMIT:
            raise ValueError("命名说明不能超过%d字" % DESCRIPTION_LIMIT)
        cleaned = {str(key): value.strip() for key, value in names.items()}
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM models WHERE model_id=?",
                                  (model_id,)).fetchone() is None:
                raise LookupError("分型版本不存在")
            current = connection.execute(
                "SELECT MAX(version) AS version FROM model_names WHERE model_id=?",
                (model_id,)).fetchone()
            next_version = (current["version"] or 0) + 1
            connection.execute(
                "INSERT INTO model_names(model_id,version,names,note,created_at) VALUES(?,?,?,?,?)",
                (model_id, next_version, encode(cleaned), note.strip(),
                 datetime.now(timezone.utc).isoformat()))
        return self.latest_naming(model_id)
