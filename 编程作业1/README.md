# 多周期需求下的多仓库车辆分配优化


### 退火参数与组合搜索补充实验

本轮对 4 个目标实例配对复测。逐例中位数合计从 **118,556.047** 降至 **118,295.508**，降低 **260.539**。通过双评分入口复测并替换正式分配的实例为 `10_random`、`13_random`。

见 [退火与重组实验](deep_candidates/anneal_v3/README.md) 和 [本轮复测表](deep_candidates/anneal_v3/paired_scores.csv)。

<!-- tuning-v2:start -->
### 2026-11-23 调优结果

本轮 9 个实例按每仓 3 秒、每方案 3 次配对复测。中位数合计由 **143,061.480** 降至 **139,581.215**，降低 **3,480.265**（**2.43%**）。

本轮更新 `07_random`、`08_random`、`09_random`、`10_random`、`12_random`、`13_random`。本轮对照为优化前的正式提交 `ce8c209`。新版候选必须在兼容评分器和原评分附件兼容入口中，均满足中位数下降且赢得至少两次配对试验。

见 [本轮逐例复测](deep_candidates/tuning_v2/paired_scores.csv) 和 [实验说明](deep_candidates/tuning_v2/README.md)。
<!-- tuning-v2:end -->
## 当前正式提交与统一展示

`released/` 是当前正式提交目录，包含九份 `.out`、对应的评分报告和 `manifest.json`。
`publish_results.py` 对原方案、各评分搜索结果和上一次正式提交进行独立复测；
每轮新建评分器，避免把搜索缓存当作重复实验。以三次中位数比较，挑战方案还需
在多数对应轮次中优于保留方案；持平时保留旧方案。限时启发式仍无全局最优保证。

```powershell
python publish_results.py --trials 3 --time-budget 1200
```

此命令同时生成 `../docs/published.js`，网页评分模式和 Streamlit 的正式提交模式
均读取本次发布结果。历史 `output/` 及 `docs/data.js` 仍用于历史滚动验证，不混用其成本。
新增回归测试运行 `python verify_improvements.py` 和 `python verify_route_pool.py`。

搜索阶段可用 `--time-budget 300` 限制整轮评分求解预算，最终独立复测次数由
`--score-trials` 控制（默认 3）。超时会保留合法的参考分配，并明确标为未完成复测，
成本不会伪造为 0。新的搜索结果需经过发布流程后才进入正式提交。

## 按 getScore.py 优化

### 进一步搜索：路线池和车型顺序

```powershell
python route_pool_search.py input_data/13_random.in --outdir deep_pool --reference released --workers 2 --defer-grading
python fleet_order_search.py input_data/13_random.in --allocation released/13_random.out --outdir deep_orders --samples 20 --direct
python measured_fleet_portfolio.py input_data/13_random.in --reference released --reference deep_pool/rank_1 --reference deep_orders/rank_1 --outdir deep_combined
python publish_results.py --file input_data/13_random.in --candidate-dir deep_pool/rank_1 --candidate-dir deep_orders/rank_1 --candidate-dir deep_orders/rank_2 --trials 3 --time-budget 600
python verify_release.py
```

路线池从不同容量组合与随机种子生成可行路线，用 SCIP 联合选择路线，按客户不重复服务、
车辆容量等级库存和路线载重约束匹配真实车辆，再用评分器筛选分车结果。它是有限候选集上的
搜索方法，没有完整 CVRP 的全局最优证明。默认使用分阶段秒数预算和候选数量上限；
`--pool-iterations` 等参数可改用固定迭代数，但复杂车队可能耗时很长。

车型顺序搜索保持每辆车的仓库归属不变，只调整 `.out` 每行的合法车辆顺序。
附件按文件顺序录入车型，固定种子和限时的启发式路线求解会受这个顺序影响。
`--direct` 对每个采样顺序按每仓 3 秒测试；缓存只用于搜索，不作为独立复测结果。
最终提交、评分报告保留原文件顺序；网页车辆清单仍可按编号排序方便阅读。

`measured_fleet_portfolio.combine_fleets` 可把已有实验中逐仓测过的有序车型组合
重新组合，用各车型的库存数量约束统一匹配真实车辆编号。它能复用历史实验结果，
避免重复求解相同的仓库子问题；其估计值也需要最后的独立评分验证。

新搜索目录中的单次低分不能直接当成稳定改善；最后的发布步骤新建评分器独立复测，
不满足多数轮次和中位数改进门槛时保留上次提交。只更新一个实例时，其余已验证实例
及网页记录会保留。路线缓存和顺序缓存均记录数据签名，输入变化后不会复用。

使用 Python 3.11 或以上版本，在本目录运行：

```powershell
python -m pip install -r requirements-score.txt
python solve.py --objective getscore --score-polish --incumbent-dir score_output --output-dir score_regenerated
```

已发布的 `.out` 文件及评分对比保存在 `score_output/`，上面的命令将重新搜索的结果写到 `score_regenerated/`。`output/` 提供原仓库方案，`--incumbent-dir` 指定本轮也要复评的上一次方案。可用 `--baseline-dir` 指定其他原始对照目录，用 `--file` 处理单个输入。

这个模式用每位客户的全历史平均需求，分别求解各仓库允许跳过客户的容量车辆路径问题。漏服务罚分为仓库到客户单程欧氏距离的 2 倍。需求和车容量同时乘以历史期数，精确保留平均需求的分数部分。距离和罚分按 1000 倍取整，搜索、算法比较、最终选优统一使用 res.cost()/1000。它是旧附件的兼容参考成本；原附件接口仍需版本桥接。

同载重车辆用动态规划搜索各仓库的车辆数。两个仓库的异构车队在组合数较小时枚举载重分配，其余实例从多个初始方案搜索跨仓移动和交换。逐仓缓存相同容量组合的成本。先用短时限筛选，再按提交文件中实际出现的车辆顺序录入车型，对最终候选和原方案按每仓 3 秒独立复评。没有足够改进证据时保留原方案。评分依赖 PyVRP 版本与墙钟时限，复跑时数值可能波动。

`--score-polish` 扩大复评分配集合。对于多仓库异构车队，从多个初始方案采样各仓库可用的车队，并用 SCIP 整数规划联合选择各仓库的车队。模型每仓选择一个车队，各车型在全部仓库的数量之和等于库存，目标为各仓库的 PyVRP 估算成本之和。每轮排除已选的组合，生成多个不同的解，再对前列解重新采样。这个过程能同时改变多个仓库，不要求每一次单车移动都能降分。

如果车队重组未替换上一次方案，则按仓库对挑选单车调拨和换车候选，并用完整评分时限复查其中较好的方案。

对比表中的 `baseline_score`、`incumbent_score` 和 `optimized_score` 分别是本轮对原方案、上一次方案和最终方案的评分。

## 组合搜索

```powershell
python -m pip install -r requirements-score.txt
python compare_algorithms.py --portfolio --file input_data/13_random.in --output-dir portfolio_run
```

`--portfolio` 会运行适用的候选搜索和加深试验，使用每仓 3 秒的评分比较结果，写入 `*_portfolio.out`。处理仓库自带实例时，也会复评已有的最佳分配。各方法的分数在同批生成的 CSV 中。

`score_benchmark/` 保存早期实验的九个分配及三份报告。该目录 `confirmed.csv` 的历史测量合计为 **144,394.947**，同批对照为 **146,388.474**；这些不是当前正式提交的本轮分数。当前结果以 `released/` 的独立复测为准。`method_scores.csv` 记录各方法与参数的原始测分，`strategy_summary.csv` 汇总初轮筛选，其中某方法缺席的实例使用计数动态规划、两仓枚举或束搜索分数作为对照。限时 PyVRP 求解的分数可能随机器负载和版本变化。

## 单独复现实验搜索

```powershell
python compare_algorithms.py --file input_data/13_random.in --output-dir experiment
python compare_algorithms.py --deeper --file input_data/12_random.in --output-dir experiment_deep
python compare_algorithms.py --wide-local --file input_data/13_random.in --output-dir experiment_wide
```

实验使用相同的均值需求目标对比需求贪心、车辆数动态规划、载重组合枚举、束搜索、车队整数规划、模拟退火、遗传搜索与路线池整数规划。按实例结构选择适用的算法。候选方案先用短时限筛选，写入 CSV 的分数使用每仓 3 秒的 PyVRP 评分。`--deeper` 对同载重车队完整评估每仓可能的车辆数，或延长小规模两仓实例的枚举筛选。`--wide-local` 按仓库对扩大调拨和交换候选，然后用每仓 3 秒评分联合重组车队。实验写入指定目录，可直接用对应的 `.out` 文件复评分数。

## 运行历史模式

历史模式只依赖 NumPy，需要显式选择。在本目录执行：

```powershell
python -m pip install -r requirements.txt
python solve.py --objective historical --output-dir regenerated
python verify.py
```

`output/` 内包含九个历史模式分配文件、各实例的 JSON 运行记录和汇总 CSV。
`regenerated/` 可用于核对重新生成的结果。
单个历史实例可用 `python solve.py --objective historical --file input_data/10_random.in --output-dir single`。
`example_tutorial.out` 是课件给出的格式示例。

## 历史模式的模型与代价

车辆一次性分配给各仓库。每期每辆车可闲置或执行一条往返路线。
正需求客户至多服务一次，需求不拆分。路线需求总和不得超过车辆载重。
代价为欧氏行驶总距离，加上每个未服务正需求客户的罚分。
按课件“往返距离的 2 倍”取单程欧氏距离的 4 倍。罚分倍数统一定义在
`refine_eval.py` 的 `UNSERVED_DISTANCE_MULTIPLIER`。

## 求解算法

1. 构造均衡、平均需求、平均需求加标准差和趋势场景四类初始分配。趋势场景保留同期需求，检测到明确上升趋势时才调整场景需求。车辆按载重从大到小分配。
2. 从已知历史均匀取 4 个周期，按固定路线顺序和多个旋转评估各方案。贪心构造容量可行路线，对跳过的客户累计罚分。预先用 2-opt 改进基础访问顺序。
3. 从样本成本最低的初始分配开始，枚举跨仓库单车移动和两车交换。按载重去重，并缓存受影响仓库的周期成本。每轮采用成本下降最多的改动，默认最多两轮。
4. 分别用前 60% 和前 80% 的历史重新生成并搜索分配，在其后各自的时间窗口取 3 期评估。滚动验证评估默认搜索流程的样本外表现。最终提交分配用全部已知历史作为取样范围。

`--train-periods`、`--eval-periods` 和 `--rounds` 可调整采样及搜索预算。
历史模式的路线与分配都采用启发式方法，汇总成本是历史场景的本地估算。
本项目使用的 `getScore.py` 评测目标见前文，不能与历史模式成本直接比较。

## 可选路线池

`python -m pip install -r requirements-optional.txt` 后可在求解命令中增加
`--cp-sat`，在最终拟合时额外加入有限路线池产生的候选。
每条候选路线的收益为它覆盖客户的总罚分减去路线长度，单客户路线也可产生收益。
车型阈值约束保证每个场景的路线可匹配到仓库拥有的异构车辆。
本机 Windows 测试中原依赖的 CP-SAT 原生求解调用发生访问冲突，随包结果由默认流程生成。

## 输入数据说明

09、12、13 号原始输入分别比文件头多 203、200、155 行客户记录。
根据文件头给出的客户数顺序读取，每组跳过的记录数写入 JSON。
原始数据按课件附件原样提交。

