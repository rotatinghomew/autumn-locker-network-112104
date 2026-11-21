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

使用 Python 3.11，资料读取不依赖第三方包。

```sh
python3 source_book.py data/samples.json
python3 -m unittest discover -s tests -v
```

资料读取命令列出各表记录数；测试检查交叉引用与格式，不生成分析结果。
