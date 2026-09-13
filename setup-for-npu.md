# NPU Chipyard 初始化与开发

本文只面向尚未 clone 本项目的新开发者。

## 首次初始化

准备好 Chipyard 1.13.0 所需的系统依赖和 Conda，然后直接 clone `npu/dev` 并运行原生 setup：

```bash
git clone --branch npu/dev \
  https://github.com/CaiTH0618/chipyard.git
cd chipyard

./build-setup.sh riscv-tools
```

不要先初始化官方 Chipyard 1.13.0，也不要在 clone 时添加 `--recurse-submodules`。当前仓库已经固定了所有定制 submodule 的版本，`build-setup.sh` 会按照 Chipyard 原生顺序初始化它们。

setup 完成后，每次开始工作前进入环境：

```bash
source env.sh
source scripts/chipyard-build-resources.sh
```

`env.sh` 和 `.conda-env` 由 setup 在本机生成，不要从其他开发者的工作目录复制。

## 在 Submodule 中开发

setup 后 submodule 显示 detached HEAD 是正常现象。需要修改某个 submodule 时，从当前位置创建 `npu/*` 功能分支；将 `<topic>` 替换为实际功能名称：

```bash
git -C generators/rocket-chip switch -c npu/<topic>
```

`.gitmodules` 使用 HTTPS，方便所有人下载。需要 push 时，在本机为该 submodule 配置 SSH push URL，然后推送分支：

```bash
git -C generators/rocket-chip remote set-url --push origin \
  git@github.com:CaiTH0618/rocket-chip.git

git -C generators/rocket-chip push -u origin npu/<topic>
```

Gemmini、`gemmini-rocc-tests`、ActiveSPM 和 FireSim 使用相同流程，并替换为相应的路径和仓库名。

如果必须直接在已有的 `npu/dev` 上开发，先确认远端分支与当前固定版本一致：

```bash
git -C generators/rocket-chip fetch origin npu/dev
test "$(git -C generators/rocket-chip rev-parse HEAD)" = \
  "$(git -C generators/rocket-chip rev-parse origin/npu/dev)"
```

`test` 成功后，再执行以下命令之一：

```bash
# 本地还没有 npu/dev
git -C generators/rocket-chip switch --track origin/npu/dev

# 本地已经有 npu/dev
git -C generators/rocket-chip switch npu/dev
```

如果 `test` 失败，应从当前版本创建新的 `npu/<topic>`，不要直接切换到已经前进的远端分支。

## 提交跨仓库修改

始终先提交并 push 最内层仓库，再逐级更新父仓库记录的 submodule commit。例如：

```text
gemmini-rocc-tests
→ gemmini
→ chipyard
```

确认子模块 commit 已经 push 后，才能在父仓库提交对应路径：

```bash
git add generators/gemmini
git commit
git push
```

这样其他开发者才能从远端获取父仓库所记录的完整版本。
