# 从 New pre-proc RTLIL 直接生成区域

`--rtl-direct` 实现独立于完整 New generic 网表的实验路径：**Base-only 契约认证 →
pre-proc RTLIL 对象差分 → 完整 process／组合实例切片 → 局部综合 → 拼接 → 对 New RTL 证明**。
原来的 `netlist`、`rtl`、`hybrid` 模式继续作为实验基线，代码入口和输入缓存相互独立。

## 运行

需要 Python 3.10+、PyYAML、原生 Yosys、与该二进制匹配的 `yosys-config`／开发头文件，
以及支持该 SDK 编译选项的 C++ 编译器。本次实测为 Yosys 0.67+24、C++20。
YoWASP 不能加载本项目的原生动态插件。

```sh
# 检查 SDK 并编译；正常运行也会自动执行并缓存此步骤。
python scripts/synthesis/build_eco_plugin.py --yosys yosys
# SDK 所指定编译器不存在时会尝试 g++；也可设置 CXX。
CXX=g++ python scripts/synthesis/build_eco_plugin.py

# 不需要下载外部 RTL 仓库的三个例子。
python scripts/benchmark/prepare_rtl_examples.py
python scripts/run_incremental_case.py rtl-assign --rtl-direct --frontend
python scripts/run_incremental_case.py rtl-process --rtl-direct --frontend
python scripts/run_incremental_case.py rtl-macro --rtl-direct --frontend

# 已准备好 eco-002 的 Base/New RTL 检出目录时。
python scripts/run_incremental_case.py eco-002 --rtl-direct --frontend
# 后续 ECO 复用已认证 Base，重新展开当前 New RTL。
python scripts/run_incremental_case.py eco-002 --rtl-direct
```

离线阶段可以完全不提供 New RTL：

```sh
python scripts/run_incremental_case.py eco-002 --rtl-direct --frontend --setup-base-only
# 准备或修改 results/eco-002/work/new/ 后，再执行在线阶段。
python scripts/run_incremental_case.py eco-002 --rtl-direct
```

`--frontend` 在此模式只表示重建 Base 离线缓存；New 每次重新展开。
`--setup-base-only` 的成功状态是 `setup_complete`，不冒充完成 ECO 的 `verified: true`。
SDK 缺失、缓存过期或验证未通过都会返回非零退出码，不会自动切换到完整 New 网表流程。

## 产物和边界

| 位置（相对 `results/<case>/`） | 内容 |
| --- | --- |
| `base/rtl_direct/base_preproc.rtlil` | Base 层次展开及 uniquify 后、proc 前的设计 |
| `base/rtl_direct/design_flat.json` | Base 完整 generic 网表 |
| `base/rtl_direct/source_index.json` | process、cell／assign、wire、连接及实例路径索引 |
| `base/rtl_direct/contracts/catalog.json` | 离线候选、通过认证的契约及不支持原因 |
| `new/rtl_direct/new_preproc.rtlil` | New 层次展开设计；未执行全局 proc／flatten |
| `new/rtl_direct/source_index.json` | New pre-proc 对象与结构语义快照 |
| `incremental/rtl_changes.json` | 源码 token 差分、宏变化和使用位置，作为解释信息 |
| `incremental/rtl_selection.json` | 实际 RTLIL 语义变化的输出目标及实例包络 |
| `incremental/new_region_preproc.rtlil` | 保留原始过程树的独立区域 |
| `incremental/new_region_spec.json` | 只对区域降低、展平所得的局部规范 |
| `incremental/new_region_synth.json` | 局部优化后的替换区域 |
| `incremental/region_contract.json` | 本次使用的 Base 认证边界、被删除单元、外部负载端 |
| `incremental/stitched.json` | 全部证明通过的拼接 candidate |
| `incremental/local_verification.json` | 区域综合证明结果 |
| `incremental/verification.json` | 对完整 New RTL 的最终证明结果及 candidate 摘要 |
| `incremental/attempts/<run>/<round>/verification/` | 验证专用的完整 New reference 和证明日志 |

**`new/design_flat.json` 不会生成，也不会被读取。** 若以前运行基线留下了该文件，
新模式保留并忽略它。三个全新小案例的该文件应根本不存在；测试另有损坏文件检查。
验证专用 `new_reference.json` 仅在 candidate 已经生成后创建，不能进入定位器、
提取器、Base 复用判断或拼接计划。只有通过全部证明的 candidate 才复制到最终产物位置。
每轮失败产物和原因保留在 `attempts/`，新运行先清除旧的成功状态及最终 candidate。

## 实现

### Base-only 认证

`run_new_elaboration.py` 对 Base 先保存 pre-proc RTLIL，再执行完整 generic 前端。
`base_cutpoints.py` 从 Base 模块端口和完整组合过程／公开表达式的输出生成候选；
从输出沿 generic 网表扇入传播，在模块输入停止。候选构建和证明期间不读取 New。

契约按 bit 保存 RTL 信号名、声明宽度、signedness、索引方向、实例路径和 Base bit。
Base 原始区域通过同一个 C++ 提取器生成局部规范，再与 Base generic 中的对应逻辑锥
执行 SAT 证明。`src`、名字和 scope 只提供候选映射，未经认证的候选不能用于拼接。
全局优化可能把模块输入连接为常量或相互别名：认证记录这些输入约束，并在拼接时
连接完全相同的 Base bit。它们不是任意新增的环境假设。

删除区域的每一个外部负载必须有明确重连。对于没有局部公开 RTL 名字的跨模块共享
表达式，保留其 Base 扇入供外部负载使用；New 区域从模块输入重新计算自己的副本。
这种处理可能复制逻辑，报告中的 `duplicated_shared_fanin` 显式记录该数量。
内部常量输出没有可唯一归属的 flat consumers，必须在 New 中保持同一个常量；
否则扩大到父包络。内部输出别名拆分、边界消失或无法认证同样触发扩大。

缓存绑定 Base 源码、递归 literal include、完整 `design.yaml`、Yosys 版本、插件摘要、
前端实现、认证实现和产物摘要。修改 Base／配置／实现后需要显式重新 setup。
New RTL 可以随在线 ECO 更新，不要求与上次 New 缓存一致。

### 语义差分与原子提取

`eco_index` 使用 Yosys 的内存对象生成结构语义快照：包含 cell 参数／连接，process
赋值、switch／case 的顺序、sync 类型与条件，以及相关语义属性。匿名 RHS 表达式
展开到消费它的对象，process 临时变量使用遍历内的稳定编号；源文件绝对路径、行号
和 Yosys autoidx 不作为语义身份。公开符号仍是边界身份，因此内部重命名可能造成
保守扩大。这是 elaborated RTLIL 的结构语义比较，**不是**完整 SV AST 等价判定或 SSA。

`eco_extract` 按实例路径在 uniquify 后的模块副本中，从目标输出向后遍历位级依赖。
process 和子实例为原子单位，完整复制 defaults、覆盖赋值、多层分支及同步规则。
只在显式认证的输入 bit 停止。实现使用 `SigMap`、RTLIL clone 和对象重写 API，
不使用正则修改 RTLIL 文本。过程之间的新组合依赖会被纳入闭包；新增使用的原有
模块输入已经纳入契约的可用输入集合，不会悬空。

提取之后先执行 `hierarchy -top incremental_region`，去掉不相关设计，再对局部层次
执行 `proc`、`flatten` 和优化。组合 case 可能由 `proc` 暂时表示为 ROM，局部
`memory_collect; memory_map` 会将这种表示转回组合逻辑。显式存储器和最终推断的
锁存器／触发器不在本版可提取范围。

接口变化提升到父模块；多处 dirty 实例合并到最近共同祖先。原子输出契约不可用、
新依赖无法闭合或证明不通过时，按“模块包络 → 父模块包络”顺序尝试，受
`--max-expansions` 限制。到达不支持的状态边界仍不能闭合时报告 `unsupported`。

### 两层证明和状态约束

局部证明比较局部优化结果与提取后的 region specification。最终证明在独立目录中
从完整 New pre-proc RTLIL 生成 reference，检查所有顶层输出和所有状态输入。
验证阶段可以做结构匹配来建立**必须被证明的**内部对应点；这些对应关系不返回增量算法。

另外将 candidate 与 Base 的状态时钟／复位／使能函数比较，禁止本版改变这些语义。
寄存器类型和初始值也必须一致。所有证明沿用项目既有语义：New 为 gold，candidate
为 gate，参考的 X 为 don't-care；不通过 `setundef` 固定 X，也不把超时／unknown 当成功。
报告分开记录 Base setup、New elaboration、区域工作及最终 verification，不能把验证
专用的完整 New 转换混入增量综合工作量或据此声称已经获得性能优势。

## 支持范围与现阶段限制

支持 assign、完整 `always_comb`、无锁存器的组合 `always`、组合子实例，以及保持在
被替换模块之外的原有寄存器。参数／宏／generate 先由 Yosys 展开；布局变化需要父包络。
输入 bit 的声明 offset／upto／signedness 必须与契约一致。

目前切割点主要是可认证的模块输入／公开输出；**还没有实现任意同模块寄存器 Q/D
处的独立 pre-proc 切割**。因此某些理论上合法的寄存器间 ECO 仍会报告不支持。
不支持状态修改、memory 结构变化、双向端口、顶层接口变化、动态 include，以及
Yosys Verilog frontend 不支持的语言构造。多个独立 dirty 区域暂时合并到共同包络，
不追求最小区域；语句级 SSA／ITE 切片和精确逐 pass provenance 仍属后续工作。

## 回归与实测

```sh
python -m pytest -q
```

新增测试覆盖三类基本案例、嵌套分支／默认值／后续覆盖、新输入和新局部生产者依赖、
uniquify 实例、子接口变化、无 statement src 的赋值修改、注释／autoidx 稳定性、
不变状态与 D 锥替换、状态控制拒绝、锁存器拒绝、Base 缓存失效、损坏 New 网表被忽略，
以及故意损坏拼接结果时最终证明必须失败。

本机 eco-002 已通过该路径：选择 `singlecycle_ctlpath`，Base 共 427 个单元，
删除 72 个、保留 355 个；9 个共享扇入单元留给区域外负载，局部重新生成所需逻辑。
这比旧 hybrid 模式的区域更粗，反映了当前认证边界粒度；不能据此声称面积或时间更优。
