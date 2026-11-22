# 多仓库车辆分配优化

九个公开实例的复测中位数合计为 **141,889.782**，低于逐实例
选取的仓库最佳对照 **143,216.963**，降幅为 **0.93%**。

- `allocations/` 各公开实例三次复测后选中的 .out。每行是车辆数及 0 基车辆编号。
- `scores.csv`   最终方案、仓库最佳对照和原始方案的复测中位数。
- `trials.csv`   每个入围方案的三次评分。相同车型序列共用每仓同一次评分。
- `attachment.csv` 通过用户附件的 evaluate_student_solution 再跑一轮的分数。
- `method_scores.csv` 各方法筛选阶段每仓三秒的分数。
- `search_candidates.json` 各次搜索记录的候选分配，按仓库列出车辆编号。

## 评分环境

源仓库是 workyy-SVG/multi-depot-vehicle-allocation，快照提交
7704e209ee4b9fdc60268df73bedc44f02483818。

附件使用旧版的 demand 接口，同时向 PyVRP 传入浮点量。PyVRP 0.7.0
接受旧接口但拒绝浮点量，仓库选定的 PyVRP 0.12.2 使用 delivery。
score_attachment.py 加载原附件并桥接 Model。需求和载重同乘历史期数，
距离和罚分同乘 1000 后四舍五入，评分还原为原距离单位。无车仓库保留附件
原始的浮点罚分。每次 PyVRP 评分仍为每仓 3 秒、默认种子 0。
从命令行传入附件、输入和输出路径。

三个仓库参考目录分别是 output、score_output、score_benchmark。
scores.csv 的 repository_median_score 取三个目录每个实例的最低中位数，
original_median_score 记录 output 的中位数。搜索候选和三组参考均参与选优。
09、12、13 号实例按文件头声明的客户数读取，和附件一致。

## 复现

测试环境使用 Python 3.12.15、NumPy 2.5.5。运行 pip install -r requirements.txt。
以下示例中的 REPO 替换为下载的仓库路径，附件路径替换为本机文件。

```powershell
python optimize.py "REPO/编程作业1/input_data/13_random.in" --outdir run13 --reference "REPO/编程作业1/output" --reference "REPO/编程作业1/score_output" --reference "REPO/编程作业1/score_benchmark" --workers 7 --rounds 2 --samples 24 --beam 3 --pair-width 8 --anneal-steps 26 --finalists 9
```

```powershell
python optimize.py "REPO/编程作业1/input_data/12_random.in" --outdir full12 --reference "REPO/编程作业1/output" --reference "REPO/编程作业1/score_output" --reference "REPO/编程作业1/score_benchmark" --workers 7 --finalists 18 --full-enumeration
```

```powershell
python score_attachment.py --scorer "getScore(2).py" "REPO/编程作业1/input_data/13_random.in" "allocations/13_random.out"
```

```powershell
python verify_scores.py "REPO/编程作业1/input_data/13_random.in" --outdir recheck --reference "REPO/编程作业1/output" --reference "REPO/编程作业1/score_output" --reference "REPO/编程作业1/score_benchmark" --search run13 --trials 3 --top 3 --scorer "getScore(2).py"
```

一次求解可传单个 .in 文件或整个输入目录。可重复传 --reference
和 --search。调整参数的例子见 profiles.txt。
每个求解进程都有自己的 PyVRP 实例，普通笔记本可减少 --workers。
3 秒限时分数可能随计算负载和版本变化。

## 算法

同载重车队用动态规划搜索全部车辆数分配。小型两仓异构车队枚举不同载重
组合。多仓异构车队从多个需求、几何和已发布方案出发，通过单车调拨、
换车、多车调拨、模拟退火和车队重组搜索。SCIP 重组模型每个仓库选择
一个候选车队，并约束每辆车恰好分配一次。低预算筛选用固定迭代数或
分段墙钟预算。3 秒复试使用用户的目标及一致的单位和 PyVRP 版本。
车辆数动态规划和载重枚举依赖 PyVRP 给出的路线成本，完整路径问题
没有全局最优性证书。
