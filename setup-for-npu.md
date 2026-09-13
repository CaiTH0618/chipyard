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

## 在个人仓库中开发

不要直接向 `CaiTH0618` 下的 Chipyard 或 submodule 仓库提交。先在自己的 GitHub 账号中 fork Chipyard，以及本次需要修改的每个 submodule。

将顶层 Chipyard 的原始远端保留为 `upstream`，并把 `origin` 指向自己的 fork；将 `<username>` 和 `<topic>` 替换为实际名称：

```bash
git remote rename origin upstream
git remote add origin git@github.com:<username>/chipyard.git
git switch -c npu/<topic>
git push -u origin npu/<topic>
```

setup 后 submodule 显示 detached HEAD 是正常现象。以 Rocket Chip 为例，将其远端切换到自己的 fork，并从当前固定版本创建功能分支：

```bash
git -C generators/rocket-chip remote rename origin upstream
git -C generators/rocket-chip remote add origin \
  git@github.com:<username>/rocket-chip.git
git -C generators/rocket-chip switch -c npu/<topic>
git -C generators/rocket-chip push -u origin npu/<topic>
```

Gemmini、`gemmini-rocc-tests`、ActiveSPM 和 FireSim 使用相同流程，并替换为相应的路径和个人仓库 URL。只需要 fork 和切换本次实际修改的 submodule。

不要修改 `.gitmodules` 来保存个人仓库 URL；这些 URL 只配置在自己的本地 Git remote 中，避免影响其他开发者初始化。

不要使用 `npu/dev` 作为个人开发分支名。`npu/dev` 是本项目提供的公共初始化基线；个人开发分支统一使用能够说明改动内容的 `npu/<topic>`。

## 提交跨仓库修改

始终先提交并 push 最内层仓库，再逐级更新父仓库记录的 submodule commit。例如：

```text
gemmini-rocc-tests
→ gemmini
→ chipyard
```

确认子模块 commit 已经 push 到自己的远程仓库后，才能在父仓库提交对应路径：

```bash
git add generators/gemmini
git commit
git push origin npu/<topic>
```

这样其他开发者才能从远端获取父仓库所记录的完整版本。
