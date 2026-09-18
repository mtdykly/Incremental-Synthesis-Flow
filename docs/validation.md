# 修复与验证记录（2026-09-15）

本次完成的是 **generic 网表的组合逻辑 ECO 替换闭环**。结果与 New
比较，不要求 Base 与 New 功能相同。

## 实际执行结果

验证环境：WSL Ubuntu、Python 3.13、原生 Yosys 0.33。
小型回归也曾使用 YoWASP Yosys 0.69 执行。
`eco-002` 最终证明在 YoWASP 0.69 上仍触发 Wasmtime 调用栈限制；本表中的
大型案例通过结果来自原生 Yosys，不将这次工具错误算作通过。

| 项目 | 结果 |
| --- | --- |
| 完整回归 | 36 项通过，包含实际综合与形式证明 |
| 最后调整 formal 候选生成后的专项回归 | 2 项通过 |
| `eco-001` 无修改对照 | 成功；保留 364 个 Base cell；替换区域为空 |
| `eco-001` 最终证明 | 3944 个等价点全部通过 |
| `eco-002` 不启用 formal 回填 | 成功；Base/New 区域为 9/7 个 cell；保留 286 个 |
| `eco-002` 启用 formal 回填 | 成功；Base/New 区域为 8/6 个 cell；保留 287 个 |
| `eco-002` formal 候选状态 | 1 个 proven，19 个 unknown；unknown 未直接复用 |
| `eco-002` 回填后最终证明 | 1437 个等价点全部通过，未证明点为 0 |
| Python 编译、Shell 语法、`git diff --check` | 通过 |

最终启用 formal 的缓存 IR 流程中，`eco-001` 总耗时约 5.17 秒，
`eco-002` 约 8.79 秒。这里包含规划、formal、局部综合、拼接与证明，
**不包含从 RTL 生成前端 IR**，不能据此报告端到端 RTL 加速比。
这些数值来自一次运行，不是统计基准。

## 如何复查

```sh
python -m pytest -q
python scripts/run_incremental_case.py eco-002 --formal
python scripts/benchmark/run_benchmark.py eco-001 eco-002 --formal \
  --output results/validation/benchmark.json
```

本地本次运行的证据：

- `results/validation/pytest-final.log`
- `results/validation/formal-final.log`
- `results/validation/benchmark.json`
- `results/eco-002/incremental/matching/formal_results.json`
- `results/eco-002/incremental/verify_stitched.log`
- `results/eco-002/incremental/run_report.json`

本机工具解压在忽略的 `.venv/native` 目录内，可在 WSL 中这样使用：

```sh
cd /mnt/e/GitHub/Incremental-Synthesis-Flow
. .venv/bin/activate
export PATH="$PWD/.venv/native/usr/bin:$PATH"
export LD_LIBRARY_PATH="$PWD/.venv/native/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
python scripts/run_incremental_case.py eco-002 --formal
```

生成文件在忽略目录内，不再提交临时工作树。`eco-001` 本次使用其已提交
的两份 `design.json` 分别执行 `hierarchy -top riscv_core; flatten;
opt_clean; write_json .../design_flat.json`，再运行对照流程。
`eco-002` 使用已提交的 `design_flat.json`。原 RTL 源仓库 URL 未随历史
gitlink 保存，因此没有把这些运行表述为从原 RTL 完整重建。新增前端在
回归中的真实 Verilog 上生成了 frontend、mapped 输出并通过验证。

## 修复内容与边界

1. 源码、作用域和拓扑只生成候选；复用须通过逐引脚连接检查。
2. 状态位 Q 别名、时钟/复位/使能、初始化检查与 D 输入修改分开处理。
3. 区域补充 New 所需的稳定输入，显式重连外部 sink；不再要求旧区域输入
   集合与新区域完全相同。
4. 处理常量、无 cell 连线、输入输出别名、共享输出及拆分别名；校验替换
   接口，并拒绝无驱动、多驱动和组合环。
5. formal 执行、状态记录、输入摘要校验、证明回填与重新规划已连通。
6. 最终证明共享寄存器当前状态，检查所有输出与转移输入；内部切点也形成
   必须证明的义务，`equiv_status -assert` 要求全部通过。
7. 统一入口遇到超时、工具错误或未证明点均返回失败。最初的大型单 miter
   曾超时，随后拆成完整的逐输出义务；这些失败没有被算作成功。

当前尚未启用 mapped gate 增量拼接。Base 前后对应工具可证明共同命名的
组合锥；涉及状态边界或缺少功能模型的 Liberty 单元时保留未知。未提供
标准单元面积、功耗或加速比结论。


## eco-002 的 undef 验证修复（2026-09-18）

`Stitchable: True` 后出现的 `Verification: unknown` 来自最终证明，局部
综合本身已成功。旧脚本的 1464 个等价点中有 1 个未证明，位于
`next_pc_select == 2'b01` 的译码逻辑。`take_branch` 的默认值为 `1'bx`。
单独对综合前后区域执行带 gold-X 通配的 miter 可以通过。

通过缩减为 3 个 mux cell 加一个下游译码器的案例确认：证明中的
`opt -full` 会改变该 mux 网络的 X 传播，即使增加 `-keepdc` 也会误报。
最终证明改用 `opt_expr -keepdc; opt_merge; opt_clean`，只做保守常量
折叠、相同 cell 合并和清理，保留全部输出、状态输入与内部等价点的义务。

同时明确方向：New 为 gold，stitched 为 gate，使用 `equiv_simple -undef`。
参考输出为确定的 0/1 时，候选必须一致；参考输出为 X 时允许候选具体化。
这是有方向的 refinement，不是四态完全相等。初始化与寄存器语义仍须
精确匹配，不使用 `setundef -zero`，也不假定只执行合法指令。
语义依据见 [Yosys EQY 的 X 传播说明](https://yosyshq.readthedocs.io/projects/eqy/en/latest/xprop.html)。
`verification.json` 记录 `proof_kind` 和 `undef_policy`。

回归覆盖参考 X 的合法具体化、反向引入 X 的拒绝、确定值错误的拒绝，
以及实际局部综合后与下游译码器组合的案例。
