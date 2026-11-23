# 循环保护盒试销分析与下期决策系统

`data/` 为离线合成业务记录。文件均为 UTF-8 的 Tab 分隔表，首行是列名，逗号分隔集合，
`-` 表示不适用，`?` 表示未取得，**二者都是显式标记，绝不解释成零**。
标识符只在各表声明的归属范围内有效（sleeve 是封签来源身份，可复用，不是独立盒号）。

## 运行

使用 Python 3.11。依赖固定在 `requirements.txt`：

```sh
python3 -m pip install -r requirements.txt
python3 inspect_packet.py                 # 仅核对表头与形状
python3 -I run_tests.py                   # 全部单元/合成数据测试（37 项）
python3 -I run_report.py data --out reports
python3 -I run_report.py data --submissions submissions_example --out reports
```

`-I`（隔离模式）下脚本自行注入仓库根路径，不依赖当前工作目录，也不会加载外部 `*.py`。

## 业务口径（关键约束如何落地）

### 1. 暴露与归属

- **只有实际外借（event=loan）贡献一次暴露**；归还、调拨、洗护、维修、报损均不计数。
- 封签可被多个盒复用；只有封签号与接收批次的匿名归还（`box=?`）由
  `circulation/attribution.py` 在完整时间线回放中枚举**全部合法归属**：
  - 一次外借至多被一个终结事件关闭，一个匿名归还只能匹配一个盒；
  - 禁止同一盒被两个店/两条流同时领回；
  - 若存在多套合法归属，下游按区间保守呈现，不挑选有利方案（见
    `tests/test_ambiguity_synthetic.py`）。
- 外借未终结却出现该盒调拨，只记“清点漏扫信号”，**不臆造归还**。
- `identify_return` 是补确认，按其 loan 定位出借盒；返仓确认滞后（received > day）
  与迟还是两本账，分列 `scan_lag` 与 `late_days`。
- 观察截止日取 `max(day, received)`，避免把已确认归还误截成仍在借用。

### 2. 损耗四拆分与双口径（`circulation/metrics.py`）

并列给出、互不为零和替代：

| 维度 | 含义 |
| --- | --- |
| return_delay | 迟还天数；丢失/回收确认前已逾期的天数单列 `overdue_before_loss` |
| wear | 磨损事件（已维修的保留维修次数，不重复计损） |
| borrower_loss | 外借人丢失（loss），按补购单价计已发现金成本 |
| unidentified | 逾期未结 + 无法归属的匿名归还，只计提风险，不得宣称丢失 |

同时输出**按使用次数**（每百次外借）与**按在营天数**（cutoff − dispatch）两套
损耗率与单位周转成本；仍在借用期（day ≤ due）与逾期未结严格区分。

### 3. renew 与 single 的比较（`circulation/comparison.py`）

- 只在两种包装都有实际暴露的**共同适用条件层**（grade × weather × condition）内比较，
  单层只有一种包装标 `no_common_support`。
- 显式报告选择偏差（renew 集中在哪些路线循环）与结构影响（洗护状态、回收路径构成）。
- 任一层存在不可辨结果或样本不足时结论为 `inconclusive`，Fisher 精确检验只作提示；
  **控制后仍无法识别的差异不得用于宣称更耐久**。

### 4. 下期计划（`circulation/planning.py`）

继续一条路线的三个硬前提：

1. 可兑现现有借还窗口（signed 必兑现，offer 可选）；
2. 共用洗护节点的路线在**同一份日历**上联合排程，heavy 专用工位包含在总槽位内，
   不允许每条路线各取最乐观回收时间；
3. 下期可避免成本（使用+洗护+风险补购）落在 ceiling 内。旧周期已确认丢失是沉没成本，
   单独列示，不与下期成本混记。

调拨前置按节点对分别取值：路线预期差值、实测调拨（M21：W→N=5）优先，
未观测点对用保守上限（可参数覆盖，见 `planning_assumptions.json`），同节点为 0。
可出借数量按逐盒物理状态（位置/洗护/在借/丢失）计算，**不用平均库存替代**。

标准场景：S0 全量、S1 只停借 R3 不关回收、S2 停 R3 并把 O3 配调替代至 R4、
S3 仅兑现 signed。停用路线附 `return_dependencies.tsv`，说明回收点是否仍对其它门店开放。

### 5. 同项目提交与决定审计（`circulation/submissions.py`）

接班人通过同一目录提交下期新外借（`new_loans.tsv`）与错归属确认
（`identity_confirmations.tsv`，格式见 `submissions_example/`）：

- 错归属确认必须落在合法归属内，否则拒绝，杜绝跨店重复领回；
- 指定盒号的新外借逐盒校验物理可用性（在库/洗护/在借/grade）；
- 审计输出：哪些决定仍有效、哪些承诺未履行及缺盒数、预算缺口属于旧周期沉没
  还是下期可避免成本。

## 输出物（`reports/`，生成产物，不纳入版本管理）

| 文件 | 内容 |
| --- | --- |
| `box_snapshots.tsv` | 逐盒物理状态、暴露、洗护状态与位置 |
| `loan_outcomes.tsv` | 逐外借结局、在借/逾期天数、终结事件 |
| `cross_store_flows.tsv` | 跨店归还/调拨/补确认，含歧义和不可辨标记 |
| `metrics.tsv` | 全局/盒型/路线的双口径损耗与周转成本 |
| `comparison_strata.tsv`、`comparison_structural.json` | 共同支持比较与结构影响 |
| `planning_scenarios.tsv`、`planning_commitments.tsv` | 场景与逐承诺排程/不可行原因 |
| `return_dependencies.tsv` | 停用路线对其它门店回收流的影响 |
| `planning_assumptions.json` | 调拨前置等全部可调假设 |
| `submission_audit.tsv`、`summary.json` | 提交审计与汇总结论 |

## 代码结构

```
circulation/
  io.py            记录加载，严格区分 - 与 ?
  attribution.py   合法归属枚举（封签复用、一对一、漏扫不臆造）
  accounting.py    逐盒/逐外借核算、跨店流量、不确定性区间
  metrics.py       四拆分损耗、双口径率与成本
  comparison.py    共同支持比较、结构影响、耐久宣称限制
  planning.py      洗护联合排程、场景比选、回收依赖
  submissions.py   同项目提交与决定有效性审计
  report.py        报告生成
tests/             真实数据与合成数据测试
```
