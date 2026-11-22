# 讲习数字素材记录

`data/` 为离线合成业务记录。文件均为 UTF-8 的 Tab 分隔表，首行是列名，逗号分隔集合，`-` 表示不适用，`?` 表示未取得，不把它们当作零。标识符只在各表声明的归属范围内有效。

## 运行与资料核对

使用 Python 3.11。分析环境另附固定版本的数值与统计依赖，可在本地虚拟环境执行 `python3 -m pip install -r requirements.txt`。

```sh
python3 inspect_packet.py
python3 -m unittest discover -s tests
```

前一条命令只检查表头和记录形状并显示行数。可传入另一目录读取同格式记录。当前没有分析、决策或业务处理结果。

## 输入表

- `assets.tsv`：corpus 是展品编号；asset 标识同一内容，可有多规格。until 排除当日，- 表示未限定；view 是 full/detail。 列顺序为 `version`、`asset`、`corpus`、`topic`、`view`、`audience`、`channel`、`since`、`until`。
- `terms.tsv`：主题别名，不用于推定许可。 列顺序为 `topic`、`phrase`。
- `requests.tsv`：paired 须有同一展品的全图加细节；diverse 须有来自两个展品的内容。 列顺序为 `request`、`day`、`audience`、`topic`、`channel`、`bundle`。
- `exposure.tsv`：slate 按实际展示顺序，propensity 为整套展示概率，? 未知；used 为两件确实均用于课堂的回执（1/0/?）。回执观察期为请求后四日。 列顺序为 `exposure`、`request`、`source`、`slate`、`propensity`、`used`、`received`。
- `adoption.tsv`：改入口时使用的收益及共同支撑约束；新一轮探索每段允许的请求量上限。 列顺序为 `entry`、`channel`、`audience`、`min_gain`、`min_support`、`horizon`、`new_requests`。
- `inquiries.tsv`：当期备课请求。 列顺序为 `request`、`day`、`audience`、`topic`、`channel`、`bundle`。
