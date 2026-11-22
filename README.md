# 循环包装在途与洗护记录

`data/` 为离线合成业务记录。文件均为 UTF-8 的 Tab 分隔表，首行是列名，逗号分隔集合，`-` 表示不适用，`?` 表示未取得，不把它们当作零。标识符只在各表声明的归属范围内有效。

## 运行与资料核对

使用 Python 3.11。分析环境另附固定版本的数值与统计依赖，可在本地虚拟环境执行 `python3 -m pip install -r requirements.txt`。

```sh
python3 inspect_packet.py
python3 -m unittest discover -s tests
```

前一条命令只检查表头和记录形状并显示行数。可传入另一目录读取同格式记录。当前没有分析、决策或业务处理结果。

## 输入表
- `boxes.tsv`：逐盒保管登记；sleeve 可能复用，是封签来源身份，不是独立盒号。 列为 `box`、`manufacturer`、`type`、`grade`、`dispatch`、`sleeve`。
- `circulation.tsv`：day 为发生日；只有出借行计一次使用；归还和修理时 - 表示不适用，? 表示未识别。 列为 `move`、`box`、`sleeve`、`loan`、`day`、`received`、`node`、`event`、`route`。
- `loan_terms.tsv`：due 是承诺归还日；condition 是出借前外壳状态，不含后见磨损。 列为 `loan`、`due`、`expected_node`、`weather`、`condition`。
- `routes.tsv`：return 是继续可用的接收点；allowed_wash 给出可选节点，预计归还时间不是已发生事实。 列为 `route`、`lend`、`return`、`allowed_wash`、`expected_days`。
- `wash.tsv`：slots 为可消毒的总盒数，special_slots 包含在总数中，仅用于需专用工位的重型盒。 列为 `node`、`day`、`slots`、`special_slots`。
- `commitments.tsv`：signed 已承诺；offer 可选择；from_day 是起借日。 列为 `booking`、`route`、`from_day`、`due`、`grade`、`count`、`state`。
- `costs.tsv`：金额单位分；ceiling 为下期整个借用方案的成本约束，可随比选配置替换。 列为 `type`、`purchase`、`use`、`wash`、`loss`、`ceiling`。
