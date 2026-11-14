# 应用接口

服务绑定本地地址，输入输出为 UTF-8 JSON。错误输入为400，未找到对象为404，超过8MB请求为413。页面与服务端共用口径，前端不另算一份统计答案。

- `GET /health`：检查启动是否成功。
- `GET /api/context`：产品配置、默认数据和已保存材料目录。
- `GET /api/data?revision=版本`：查看完整原始表。
- `POST /api/datasets`：传入 `label` 与完整 `data`，校验后返回不可变 `revision`。
- `POST /api/explore`：传入可选的 `revision` 和 `selection`，返回实际选择、资料指纹与 `view`。现有 view 的 summary、rows、series、notes 只用于描述。
- `POST /api/notebooks`：传入选择与 `title`，由服务端重新生成描述后保存，不接受客户端伪造结果。
- `GET /api/notebooks` 与 `GET /api/notebooks/编号`：目录以及冻结资料、选择和结果。

## 历程分型接口

分型学习、人工命名与接访应用各自版本化，前端不另算统计答案：

- `POST /api/models/train`：传入可选 `revision`（默认当前数据版本）与 `params`（`bootstraps` 自助次数、可选 `k`、可选 `weights`），服务端完成特征抽取、类数选择、自助稳定性与窗长检验，返回不可变 `model_id` 与完整模型视图。同资料同参数重复训练返回同一 `model_id`。
- `GET /api/models`：已发布分型版本目录（模型指纹、数据版本、创建时间）。
- `GET /api/models/模型id`：单个模型视图，含各类代表路径、组内差异、类数选择证据与当前人工命名。
- `GET /api/models/模型id/stability`：稳定性验证报告，含候选 k 指标、自助 ARI/共分配率、窗长截断检验与拒识策略。
- `GET /api/models/模型id/names`：该模型只追加的人工命名版本历史。
- `POST /api/models/name`：传入 `model_id`、各类 `names` 与 `note`，追加一个命名版本；旧名称不覆盖、不删除。新模型的命名版本从 0 开始，不继承旧模型名称。
- `POST /api/models/assign`：传入 `model_id`，可选完整 `data`（先校验并版本化，缺省使用训练资料）、`label` 与 `household`（缺省返回全部户）。返回每户的 `cluster_id`（拒识时为 `null`）、`status`、`reject_reasons`、距离分量分解与同类相似户依据。

新增分析可扩展接口，但已有导入、描述和历史读取仍供业务使用，不能静默改换其含义。
