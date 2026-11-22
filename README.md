# multi-depot-vehicle-allocation
算法设计课程作业：多周期需求下的多仓库车辆分配优化

正式提交文件位于 `编程作业1/released/`，`manifest.json` 记录每个输入对应的
答案哈希、选中来源及独立复测结果。网页默认展示这份正式提交，同时保留独立的
“历史滚动验证”模式；两种模式的需求和罚分定义分别标注。

使用 Python 3.11 或以上版本，在 `编程作业1` 目录运行：

```powershell
python -m pip install -r requirements-score.txt
python verify.py
python verify_improvements.py
python publish_results.py --trials 3 --time-budget 1200
python verify_release.py
```

如需重新搜索，再把搜索结果加入发布候选：

```powershell
python solve.py --file input_data/13_random.in --score-polish --incumbent-dir released --output-dir new_search --time-budget 300
python publish_results.py --candidate-dir new_search --trials 3 --time-budget 1200
```

`solve.py` 默认使用与附件一致的均值需求目标；历史模式需显式添加
`--objective historical`。`--time-budget` 是整轮评分求解共享的预算，使用独立进程
停止超时的原生求解；文件读写和报告整理仍需要少量额外时间。超时会保留合法答案，
标为未完成本轮复测，成本留空，不能当作已验证结果发布。求解或发布命令预算耗尽时退出码为 2。

打开 `docs/index.html` 查看静态网页；运行 `python -m streamlit run streamlit_app.py`
查看问答页。DeepSeek 密钥仅通过 Streamlit Secrets 配置，不放入静态网页。
静态页带入的问题只预填，需在问答页手动发送。

在项目根目录安装 `requirements-dev.txt` 后，可运行 `python verify_web_app.py`
检查 Streamlit 的两种模式与问题预填。该测试使用占位密钥，并禁止真实模型请求。
