# 资料与统计口径

`contacts` 每条是一次上报，`source_key` 相同表示重复登记，`contact` 仍为不可丢失的记录号；`day` 为相对项目日，`reported_at` 为可见日期。`registered_day`、`prior_history` 描述左端已使用与新入组区别，不能统一以第一条接触重置观察起点。`coverage` 规定记录覆盖和内部停记时段；覆盖内没有接触与覆盖缺口不同。时间单位为天，duration 为服务分钟。`early/late` 是观察长度不同的登记组，不是已知类别标签。现有页仅显示接触次序，分类、类数及新户归类均未实现；分析应保留训练资料指纹、表示参数、原型和人工名称版本。

## 原始表

- `households`：150条示例，主键为 `household`。字段：`household`, `cohort`, `channel`, `registered_day`, `prior_history`, `end_day`。
- `contacts`：1050条示例，主键为 `contact`。字段：`contact`, `household`, `day`, `service`, `duration`, `reported_at`, `source_key`。
- `coverage`：150条示例，主键为 `span`。字段：`span`, `household`, `start`, `stop`, `status`, `gap_start`, `gap_stop`。

## 版本与扩展

`fixtures/data.json` 是完整导入材料，`product.json` 是当前浏览器默认选择。全量导入校验后按规范化内容指纹保存，相同内容不重复建版本。原始事实不原地修改；资料修订形成新版本。`null` 表示明确未观测，不因筛选或合计自动变为0。

`service_sequences/domain.py` 是领域入口，现阶段仅做字段校验及资料描述；`service.py` 连接范围选择、数据版本与服务端计算后的存档；`storage.py` 管理不可变数据和保存材料；`web/app.js` 提供浏览和导出交互。保存的材料同时包含原数据与选择，打开旧材料不重算为新数据。新增分析不限内部组织方式，但不能丢失现有原始记录核对与历史材料读取能力。统计方法、模型选择和业务结论没有预设答案。
