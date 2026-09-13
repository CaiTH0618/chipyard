# NPU Chipyard 初始化与开发

## 项目基线

本项目是在 Chipyard 1.13.0 基础上继续开发的定制版本，`npu/dev` 是当前正式的、可复现的初始化基线。新开发者应直接 clone `npu/dev`，不需要、也不应该先 checkout 官方 Chipyard 1.13.0、完成官方 setup 后再切换到 NPU 分支。

顶层仓库和各个定制 submodule 的精确版本组合由父仓库记录的 gitlink SHA 决定。各级 `.gitmodules` 中的 URL 只决定从哪个远程仓库获取这些 Git 对象。

## 新开发者初始化

先准备好 Chipyard 1.13.0 所需的系统依赖和 Conda，然后执行：

```bash
git clone --branch npu/dev \
  https://github.com/CaiTH0618/chipyard.git
cd chipyard

./build-setup.sh riscv-tools

source env.sh
source scripts/chipyard-build-resources.sh
```

不推荐在 clone 时使用 `--recurse-submodules`。Chipyard 原生 setup 对 submodule 有专门的初始化顺序：它会选择性跳过部分大型依赖，分别处理 Gemmini 及 `gemmini-rocc-tests`、Rocket Chip 和 FireSim，并按 Chipyard 1.13.0 的既有流程处理 toolchain、FireMarshal 和 CIRCT。

`env.sh` 和 `.conda-env` 是 setup 在本机生成的内容，不应从其他开发者的工作目录复制。

## Submodule 的版本语义

普通初始化命令：

```bash
git submodule update --init
```

不会根据远程分支选择版本。其行为是：

- `.gitmodules` 中的 `url` 决定从哪个远程仓库获取对象；
- 父仓库中的 gitlink SHA 决定最终 checkout 的精确 commit；
- 初始化后的 submodule 通常处于 detached HEAD，这是为了保证版本可复现的正常状态；
- 它不会自动选择或 checkout `npu/dev`。

不要把下面的命令用于常规初始化或更新：

```bash
git submodule update --remote
```

该命令会跟随远程分支，使同一个顶层 commit 在不同时间可能得到不同的 submodule 内容。

## 更新已有工作目录

先提交或妥善保存各仓库中的本地修改，然后在顶层仓库执行：

```bash
git pull --ff-only
git submodule sync --recursive
git submodule update --init
git submodule sync --recursive
```

第一次 `sync` 更新顶层 submodule URL，`update` 将直接 submodule 移动到顶层新记录的 SHA；第二次 `sync` 再读取更新后的嵌套 `.gitmodules`，从而同步 Gemmini 内部的 `gemmini-rocc-tests` URL。之后运行原生 setup 时，嵌套 submodule 会使用新的 fetch URL。

## 开始 Submodule 功能开发

setup 后的 detached HEAD 就是父仓库锁定的基线。默认从该位置创建新的功能分支，其中 `<topic>` 必须替换为实际功能名称：

```bash
git -C generators/rocket-chip switch -c npu/<topic>
git -C generators/rocket-chip push -u origin npu/<topic>
```

Gemmini、`gemmini-rocc-tests`、ActiveSPM 和 FireSim 使用相同规则。顶层和各 submodule 的功能分支名不要求一致；只有在确实需要跨仓库联动时才建议使用同名分支。

如果需要直接进入已有的 `npu/dev`，应先确认远程分支仍与父仓库锁定的 commit 一致：

```bash
git -C generators/rocket-chip fetch origin npu/dev

git -C generators/rocket-chip rev-parse HEAD
git -C generators/rocket-chip rev-parse origin/npu/dev
```

两条 `rev-parse` 输出一致时，可以切换到开发分支：

```bash
git -C generators/rocket-chip switch npu/dev
```

如果本地还没有该分支，则创建对应的 remote-tracking branch：

```bash
git -C generators/rocket-chip switch --track origin/npu/dev
```

如果两个 SHA 不一致，不要直接切换到已经前进的远程分支；应从当前 detached HEAD 创建新的 `npu/<topic>`，避免无意中改变父仓库定义的完整项目版本。

## Remote 约定

定制仓库使用以下约定：

```text
origin   = CaiTH0618 下的 NPU 协作仓库
upstream = 原生开源项目，可选
```

`.gitmodules` 使用公开可读的 HTTPS URL，因此只进行 clone 和构建不需要 SSH key。需要 push 时，可只在本机为 `origin` 配置 SSH push URL：

```bash
git -C generators/rocket-chip remote set-url --push origin \
  git@github.com:CaiTH0618/rocket-chip.git
```

如需跟踪官方 Rocket Chip，可以另外添加 `upstream`：

```bash
git -C generators/rocket-chip remote add upstream \
  https://github.com/chipsalliance/rocket-chip.git
```

其他定制 submodule 按相同方式设置各自的 `origin` push URL 和官方 `upstream`。不要为了配置个人 push 权限而修改已提交的 `.gitmodules`。

## 跨仓库提交顺序

涉及 submodule 的修改必须由内向外提交和推送：

```text
最深层 submodule 提交并 push
→ 父 submodule 更新 gitlink、提交并 push
→ 顶层 Chipyard 更新 gitlink、提交并 push
```

例如 Gemmini 软件和硬件同时变化时：

```text
gemmini-rocc-tests
→ gemmini
→ chipyard
```

父仓库不得引用尚未推送的子模块 commit，否则其他开发者无法获取父仓库记录的完整版本。
