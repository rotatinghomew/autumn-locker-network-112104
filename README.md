# 景点寄存联营的尾部风险与分柜保障

寄存合作方根据团期与预约窗比较柜型保障。所有时刻为当地时刻，柜号全网唯一。

## 资料交换

`data/samples.json` 是合成样册。`fields` 给出有序列名，`rows` 按该顺序记录数据，`meaning` 标明单位或来源区别。没有填写的值用 `null` 表示。

- `lockers`：开启时段，同柜可被多个策略引用。 列：`locker`, `station`, `kind`, `since`, `until`。
- `calendar`：同一团期与雨况下的记录应联合观察。 列：`period`, `date`, `weather`, `teams`, `data_as_of`。
- `usage`：未取、取消、失约后来与提前取为不同观测。 列：`ticket`, `period`, `group`, `locker`, `placed`, `collected`, `status`。
- `movements`：站间单程所需分钟。 列：`from_station`, `to_station`, `minutes`。
- `policies`：备选共享策略，min_buffer 为换站预留分钟。 列：`policy`, `station`, `substitutes`, `min_buffer`, `risk_ceiling`。
- `bookings`：can_move 是家庭接受的替代站点；已签须按原条件处理。 列：`booking`, `station`, `locker`, `group`, `kind`, `start`, `collect_by`, `can_move`, `state`。
- `outages`：当前团期的停用窗。 列：`locker`, `start`, `end`, `seq`。
- `demand`：无柜等候和成功寄存均记录申请；相同团期以 period 联结。 列：`ref`, `period`, `group`, `station`, `kind`, `arrived`, `expected_pickup`。

## 检视

使用 Python 3.11，资料读取与分析均不依赖第三方包。

```sh
python3 source_book.py data/samples.json
python3 risk_book.py data/samples.json                   # 中文决策摘要
python3 risk_book.py data/samples.json --json out.json   # 完整可刷新报告
python3 -m unittest discover -s tests -v
```

资料读取命令列出各表记录数；`risk_book.py` 在同一份交换资料上生成
团期级尾部风险比较与停用分柜报告，可随资料更新反复重跑（报告含来源
sha256 与生成时间）。

## 分析口径（joint_lockers/）

- 最小观测单位是**团期**而非单柜：同团、同一场雨引起的两站联动只算一次
  联合事件，不拆成单柜独立概率；报告同时给出独立口径对照值，仅用于展示
  联动被稀释的方向，不参与限额。
- 四类情形分开：无预约/无柜等候（demand 无对应凭证）、明确取消、
  失联未释放（失约后返回保留双凭证与堵柜段）、到 `data_as_of` 仍占柜。
- 天气分层，每层仅 2–3 个团期，所有比率带“低可信”标记；从未出现的合流
  条件估计值保留 `null`，仅给 3/n 单边上界，不以 0 进入限额。
- 尾部模拟按实际柜型与领取窗逐单判定；换站有效须同时满足家长同意站
  （can_move）、柜型一致、可用时段完整覆盖、路程+min_buffer 不突破原
  collect_by；任一不满足即人工缺口，并量化未覆盖分钟。
- 备用柜（L5）被两站策略同时引用仍是单实体，按先旧约后新单串行占用，
  绝不双计；正常小柜预约保持锁定，不取消来腾挪。
- 追溯：`traceability.by_period`（团期→事件/凭证/限额）、
  `by_booking`（预约号→分柜结果与拒因）、`adjustment_ledger`
  （每次调整影响了谁）。

