# 资料与统计口径

`contacts` 每条是一次上报，`source_key` 相同表示重复登记，`contact` 仍为不可丢失的记录号；`day` 为相对项目日，`reported_at` 为可见日期。`registered_day`、`prior_history` 描述左端已使用与新入组区别，不能统一以第一条接触重置观察起点。`coverage` 规定记录覆盖和内部停记时段；覆盖内没有接触与覆盖缺口不同。时间单位为天，duration 为服务分钟。`early/late` 是观察长度不同的登记组，不是已知类别标签。分析保留训练资料指纹、表示参数、原型和人工名称版本。

## 原始表

- `households`：150条示例，主键为 `household`。字段：`household`, `cohort`, `channel`, `registered_day`, `prior_history`, `end_day`。
- `contacts`：1050条示例，主键为 `contact`。字段：`contact`, `household`, `day`, `service`, `duration`, `reported_at`, `source_key`。
- `coverage`：150条示例，主键为 `span`。字段：`span`, `household`, `start`, `stop`, `status`, `gap_start`, `gap_stop`。

## 历程分型口径

分型在 `service_sequences/features.py`、`clustering.py`、`pipeline.py` 中实现，关键约定如下：

- **重复上报**：按 `source_key` 去重得到服务事件，但保留 `duplicate_count/duplicate_ratio` 作为数据质量特征，原始 `contacts` 一条不删。短历程（事件少）与重复上报都不会被并入一个“看起来稳定”的类。
- **无服务的两种含义**：覆盖内没有接触表达为前导/尾随沉默（`leading_silence_ratio`、`trailing_silence_ratio`）；`coverage` 的 `gap_start/gap_stop` 是未观测区间，单独计入 `uncovered_ratio`，两者不互相换算。零接触户仍是可分型对象，并在归类环节优先进入“不适合归类”。
- **先后与间隔**：服务先后用服务类型序列的 Levenshtein 编辑距离比较；接触节奏用相邻事件间隔的对数分箱概率分布与 Jensen-Shannon 散度比较；窗长、沉默、缺口、先验使用、登记延迟、重复率等 17 个结构特征经中位数/四分位距稳健标准化后取欧氏距离。三个分量按固定权重（默认 0.4/0.3/0.3）加权，权重写入模型制品。
- **观察窗归一化**：所有长度类特征除以该户自己的观察窗长度，`early/late` 因此可在同一尺度比较；跨月份另做截断检验（见下），不把窗长差异当作路径差异。
- **聚类与代表路径**：确定性 k-medoids（BUILD 初值 + 类内 medoid 重选），medoid 是真实户，代表路径即该户的真实服务序列与接触日。类数在 2–8 间扫描，按轮廓系数与固定种子自助稳定性（ARI 均值 ≥ 0.80、共分配率 ≥ 0.85、最小类 ≥ 3 户）共同选择；没有稳定候选时推荐结果会明确标注，不包装成定论。
- **稳定性验证**：模型制品内保存每个候选 k 的轮廓与自助指标、窗长截断检验（早期户截断到晚期窗长后重训并对齐标签的 ARI 与移动户清单），供复核区分真实路径变化与观察窗长短变化。
- **新户归类与拒识**：新户到各类 medoid 的混合距离决定最近类；当距离超过训练户 95 分位阈值，或最近与次近类的相对边际不足 8% 时返回“不适合归类”（`cluster_id=null` 与 `reject_reasons`），并附同类最近邻户与三分量距离分解作为“为什么接近”的依据。

## 版本与扩展

`fixtures/data.json` 是完整导入材料，`product.json` 是当前浏览器默认选择。全量导入校验后按规范化内容指纹保存，相同内容不重复建版本。原始事实不原地修改；资料修订形成新版本。`null` 表示明确未观测，不因筛选或合计自动变为0。

`service_sequences/domain.py` 是资料描述入口；`features.py/clustering.py/pipeline.py` 是分型学习与归类入口；`service.py` 连接范围选择、数据版本、模型版本与服务端计算后的存档；`storage.py` 管理不可变数据、模型、命名版本和保存材料；`web/app.js` 提供浏览、分型和接访交互。保存的材料同时包含原数据与选择，打开旧材料不重算为新数据。模型按学习产物指纹内容寻址，同一资料与参数重复训练不产生新版本；人工命名是 `model_names` 表中只追加的命名版本，新数据训练得到新 `model_id`，不会继承或重排旧模型的类别名称。新增分析不能丢失现有原始记录核对与历史材料读取能力。
