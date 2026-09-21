# 增量综合流程

本项目实现组合逻辑 ECO（工程变更）增量综合，支持 **Base 认证边界 + New pre-proc RTLIL 直接提取**，并保留展平 Yosys generic 网表差分流程作为基线。流程保留 Base（基准版本）的单元，对 New（新版本）中尚未确定可复用的区域进行综合，重新连接区域外的负载端，并证明最终结果与 **New** 等价。Base 与 New 在功能上可以有意存在差异。

## 安装与复现

需要 Python 3.10 及以上版本、支持 SAT、ABC 和 `flatten -scopename` 的 Yosys，以及以下 Python 依赖：

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
# 如果没有原生 Yosys，可为小型测试用例安装：pip install yowasp-yosys
# 然后为下方命令添加 --yosys yowasp-yosys 参数。
```

运行 `eco-002` 时请使用原生 Yosys：YoWASP 0.69 在最终证明阶段触及了 Wasmtime 的调用栈限制，而原生 Yosys 0.33 成功完成了证明。已测试的本地环境见[验证记录](docs/validation.md)。

历史版本中的源码 gitlink 未提供仓库 URL。请提供实际的 RTL 仓库 URL；获取脚本会在使用前验证所有用例引用的提交：

```sh
bash scripts/benchmark/fetch_source.sh <rtl-repository-url>
bash scripts/benchmark/checkout_case.sh eco-002
python scripts/run_incremental_case.py eco-002 --frontend
```

在默认网表差分基线中，`--frontend` 使用相同且确定的源文件列表和 Yosys 处理步骤，重新生成两份输入网表。不加此参数时，命令使用已有的 `results/<case>/{base,new}/design_flat.json` 文件。即使缺少源码检出目录，也可使用仓库中提交的 JSON 文件复现通用网表流程：

```sh
python scripts/run_incremental_case.py eco-002
```

只有区域提取、局部综合、结构检查和最终 SAT 证明全部成功，`results/<case>/incremental/run_report.json` 才会记录 `status: success` 和 `verified: true`。工具错误、超时或证明失败均返回非零退出码。每次调用都会覆盖上一次运行的状态。

## 从 RTL 修改直接生成替换区域

使用 `--rtl-direct`：New 只做 `read_verilog; hierarchy; uniquify`，由原生 Yosys
插件在 `proc` 前复制完整组合 process 及依赖，单独综合后与 Base 拼接。
定位、边界生成和拼接都不需要 `new/design_flat.json`。完整 New 的降低只发生在
最后的隔离验证目录。Base 的候选边界在读取 New 之前独立通过 SAT 认证。

```sh
# 需要原生 Yosys、匹配的 yosys-config/开发头文件和 C++ 编译器。
python scripts/benchmark/prepare_rtl_examples.py
python scripts/run_incremental_case.py rtl-assign --rtl-direct --frontend
python scripts/run_incremental_case.py rtl-process --rtl-direct --frontend
python scripts/run_incremental_case.py rtl-macro --rtl-direct --frontend
# 已有 eco-002 源码检出目录时：
python scripts/run_incremental_case.py eco-002 --rtl-direct --frontend
```

后续运行省略 `--frontend` 复用 Base 契约；`--setup-base-only --frontend` 可单独执行
离线 setup。New 每次重新展开。接口／边界变化提升到父包络，状态控制变化和无法
认证的区域明确停止。此版尚未实现任意寄存器 Q/D 切割或语句级 SSA。
详见 [RTL-direct 数据流、契约、证明与限制](docs/rtl-direct.md)。

## RTL 引导网表差分基线

原有 RTL 引导基线把源码差异作为种子，经实例来源映射和网表边界检查生成
重综合区域。该基线使用完整的 New generic 网表进行安全补全、区域提取和最终
验证，与上面的 `--rtl-direct` 独立，也不声称找到最小区域。

```sh
python scripts/run_incremental_case.py eco-002 \
  --frontend --rtl-guided --seed-mode hybrid --formal --max-expansions 5
```

首次使用应加 `--frontend`，生成优化前层次化 `elaborated_hier.json`、来源摘要
`source_manifest.json` 和带 scope 的展平网表。缓存运行时省略 `--frontend`，
但 RTL、配置和网表摘要必须与前端记录一致；过期或缺失来源信息会被拒绝。

| 模式 | 初始区域 | 安全边界 |
| --- | --- | --- |
| `netlist` | 原网表差分区域 | 保持原流程，无需 RTL 检出目录 |
| `rtl` | RTL 源码／宏／上下文种子 | 未通过复用检查的 cell 仍必须补入区域 |
| `hybrid` | RTL 种子与网表差分区域的并集 | 同样保留状态检查和最终证明 |

不传新选项时保持 `netlist`；`--rtl-guided` 默认选 `hybrid`。
`--formal` 是可选的候选证明回填，最终拼接证明始终执行。
`--max-expansions` 限制初次验证失败后的扩张次数；只有存在可映射的未证明点时，
才扩大组合扇入并重试。时钟／复位／使能函数发生未确认变化时，RTL 模式停止。

`results/<case>/analysis/` 中的 `rtl_changes.json`、`source_index_*.json`、
`rtl_seed_cells.json` 分别解释修改、来源和种子原因；`rtl_region.json` 对比源码
种子与网表差分参考区域，`rtl_experiment.json` 保存工具版本、输入摘要、区域大小、
耗时及验证结果。每次验证的日志、证明点映射和区域计划保存在运行报告指向的
`incremental/attempts/` 子目录中。

详见 [RTL 引导实现与限制](docs/rtl-guided.md)。

## 网表差分基线的正确性模型

- 源码位置、作用域、名称和拓扑指纹用于提出候选单元对应关系，**不能**证明单元可复用。输入引脚检查会比较常量、对应的主输入，以及驱动单元输出端口和位的对应关系。
- 只有明确支持的对称运算才允许交换完整操作数；减法、移位和多路选择器的输入必须保持顺序。
- 寄存器 Q 端的别名用于提供候选状态对应关系，也支持命名向量的切片。参数、位宽、时钟／复位／使能输入的对应关系和初始值必须一致，D 端可以变化。无法确定对应关系的状态和存储器会阻止计划生成。
- 尚未确定可复用的组合逻辑单元构成一个保守的闭合区域。New 的切割边界输入可以增加来自保留的 Base 单元的信号源；新旧输入集合不必完全相同。此流程不追求全局最小区域。
- 每个保留单元的输入和顶层输出都会获得明确的 New 连接。即使没有插入任何单元，也会表示不涉及单元增删的重连、常量、输入／输出别名，以及输出合并和拆分。
- 替换区域的端口必须保持名称、方向和位宽。分配新 ID 时会考虑网络名称（netnames）。无歧义的别名会被更新；失效或发生拆分的内部别名会被移除。重复驱动、悬空负载端和组合逻辑环路均无法通过结构检查。
- 最终证明让对应寄存器共享任意当前状态的 Q 位，并比较所有顶层输出，以及每个 D／时钟／复位／使能输入。寄存器语义和初始值一致构成归纳基础，状态转移函数相等构成归纳步骤。SAT 会逐项检查输出证明义务，`equiv_status -assert` 要求所有证明均通过；未经证明的结果不能计为成功。保留单元输出之间的显式对应关系会引入额外的内部证明义务，以便将算术逻辑锥的证明限制在局部范围；这些义务也必须全部通过。此流程不采用有界仿真。

支持的状态单元包括 `$dff`、`$dffe`、`$adff`、`$adffe`、`$sdff`、`$sdffe` 和 `$sdffce`。不支持寄存器重定时、状态重编码、存储器、双向（inout）端口和顶层接口变更，遇到这些情况会拒绝处理。证明采用 Yosys 的逻辑语义，不涉及晶体管时序或亚稳态模型。

## 匹配证明与分步命令

```sh
python analysis/run_incremental.py plan eco-002 --formal
# 也可导入与当前输入网表的精确摘要绑定的证明结果：
python analysis/run_incremental.py plan eco-002 --formal-results <formal_results.json>
yosys -s results/eco-002/incremental/synthesize_region.ys
python analysis/run_incremental.py stitch eco-002
```

`stitch` 会自行执行验证，并拒绝使用已过期的计划。形式验证任务会记录 `proven`（已证明）、`disproven`（已证伪）、`unknown`（未知）或 `tool_error`（工具错误），以及共享的符号输入、命令和日志。只有满足引脚布局兼容、信号源对应要求的一对一**局部单元**证明，才能允许复用。仅证明整个逻辑锥等价，并不足以支持在上游输入发生变化时保留某个单元。未能确定的候选单元会被重新构建；若位于状态边界，则会阻止计划生成。

## 前端、映射与 Base 对应关系

```sh
python scripts/synthesis/run_frontend.py eco-002 base
python scripts/synthesis/run_frontend.py eco-002 base --mapped
# 面向特定工艺的全量综合基线：
python scripts/synthesis/run_frontend.py eco-002 base --mapped --liberty <cells.lib>
python formal/build_base_correspondence.py \
  <design_flat.json> \
  <mapped.json> \
  --top riscv_core \
  --output results/base-correspondence
```

前端将 `design_flat.json` 输出为标准的展平通用网表，同时生成同一综合阶段的 `design_flat.rtlil` 和 `design_flat.v` 表示。可选的映射阶段会输出 `mapped.json` 和 `mapped.v`。如果未提供 Liberty 文件，映射输出包含的是 Yosys 通用逻辑门，而非特定工艺的标准单元。

Base 对应关系发现流程以共享主输入为基础，证明两侧同名的**组合逻辑**锥等价，并记录未知的状态边界。该流程支持 Yosys 内部单元；外部 Liberty 单元需要提供功能模型。对应关系发现仅用于诊断，不支持映射后门级网表的拼接。目前经过验证的增量实现仍基于通用网表。

## 测试与测量

```sh
python -m pytest -q
python scripts/benchmark/run_benchmark.py eco-001 eco-002 --frontend
```

测试覆盖实际匹配器、连线／别名反例、寄存器重命名和复位变更、实际局部综合、SAT 证明成功与失败的情况、证明结果合并、统一运行入口、前端生成，以及组合逻辑的 Base 对应关系。如果 PATH 中既没有 `yosys` 也没有 `yowasp-yosys`，依赖 Yosys 的测试会明确标记为跳过。

报告包含各阶段及总耗时的实测值、实际保留的 Base 单元、区域大小和验证状态。使用缓存前端结果的耗时会被明确标注，不能视为从 RTL 开始的端到端耗时。没有可比较的实测基线时，Liberty 面积和加速比保持为 null。通用单元数量不等同于面积。在使用历史日志或将 `eco-001` 视为功能 ECO 实验之前，请先阅读[用例说明](benchmarks/cases/README.md)。
