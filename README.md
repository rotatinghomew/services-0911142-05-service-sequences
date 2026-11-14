# 居家服务历程分型台

这是供服务规划人员核对资料、查看业务范围和留存讨论依据的本地应用。当前提供按户排列的服务接触历程，并支持导入、图表、原始记录、保存与重新打开；在此基础上提供可版本化的历程分型：综合服务先后、接触间隔、覆盖内沉默与未观测缺口形成混合距离，以确定性 k-medoids 产出带真实代表路径的类别，类数选择同时报告轮廓系数与固定种子自助稳定性，并通过观察窗截断检验区分路径变化与窗长效应。接访页可把新户放入已发布分型，允许“不适合归类”并给出相似户依据；人工命名独立留痕，新数据训练不会重排旧类别名称。示例为独立生成的虚构数据，不含真实家庭、医疗资料或对外服务凭据。

## 使用环境

使用 Python 3.11，独立容器镜像锁定到 3.11.14。页面为原生浏览器界面，数据处理通过本地 HTTP 服务完成，持久化使用应用内置数据库。基础浏览不依赖数值库；研究扩展所需的数值计算和建模库已按 `requirements.txt` 固定所有传递版本。依赖安装及镜像构建只在环境准备时做，启动和检查不访问网络。

```bash
python3 -m pip install -r requirements.txt -r requirements-build.txt -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 -m service_sequences --host 127.0.0.1 --port 8080
```

浏览器打开本地8080端口。容器方式先准备镜像，运行时不需要其他服务：

```bash
docker build -t service_sequences:local .
docker run --rm -p 8080:8080 -v service_sequences-state:/app/.state service_sequences:local
```

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m build --wheel --no-isolation
python3 tests/browser_smoke.py
```

检查包含真实领域字段、非法导入、边界数据保留、HTTP交互、版本指纹及重启后存档，并覆盖分型的重复上报处理、沉默与缺口区分、距离与确定性聚类、类数选择依据、自助与窗长稳定性、拒识归类、模型与命名版本化以及页面训练-命名-接访全链路。新增能力需要补充针对统计识别、支持范围和页面一致性的回归。运行检查使用临时状态，不改动资料。

字段语义与现有接口分别见 `docs/data-contract.md` 和 `docs/http.md`。短时操作见 `docs/walkthrough.md`，环境准备不包含在正式操作中。
