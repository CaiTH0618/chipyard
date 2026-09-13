# NPU Chipyard 初始化与开发

本文只面向尚未 clone 本项目的新开发者。

## 首次初始化

准备好 Chipyard 1.13.0 所需的系统依赖和 Conda，然后直接 clone `npu/dev` 并运行原生 setup：

```bash
git clone --branch npu/dev https://github.com/CaiTH0618/chipyard.git
cd chipyard
./build-setup.sh riscv-tools
```

不要先初始化官方 Chipyard 1.13.0，也不要在 clone 时添加 `--recurse-submodules`。当前仓库已经固定了所有定制 submodule 的版本，`build-setup.sh` 会按照 Chipyard 原生顺序初始化它们。

setup 完成后，每次开始工作前进入环境：

```bash
source env.sh
source scripts/chipyard-build-resources.sh  # 配置构建并行度和 JVM 内存
```

`env.sh` 和 `.conda-env` 由 setup 在本机生成，不要从其他开发者的工作目录复制。

## 开始开发

无论是否具有仓库写入权限，都不要使用 `npu/dev` 作为开发分支名。`npu/dev` 只作为公共初始化基线；请将 `<topic>` 替换为改动内容，并创建 `npu/<topic>` 分支。

setup 后 submodule 显示 detached HEAD 是正常现象。只需要为本次实际修改的 submodule 配置远端和创建分支。

### 是 `CaiTH0618` 相关仓库的 Collaborator

Collaborator 可以向对应的 `CaiTH0618` 仓库提交。保留现有 `origin`，为顶层 Chipyard 配置 SSH push URL，然后创建并推送功能分支：

```bash
git remote set-url --push origin git@github.com:CaiTH0618/chipyard.git
git switch -c npu/<topic>
git push -u origin npu/<topic>
```

以 Rocket Chip 为例，对需要修改的 submodule 执行：

```bash
git -C generators/rocket-chip remote set-url --push origin git@github.com:CaiTH0618/rocket-chip.git
git -C generators/rocket-chip switch -c npu/<topic>
git -C generators/rocket-chip push -u origin npu/<topic>
```

Gemmini、`gemmini-rocc-tests`、ActiveSPM 和 FireSim 使用相同流程，并替换为相应的路径和 `CaiTH0618` 仓库 URL。每个仓库的权限独立判断：只对具有 Collaborator 权限的仓库使用本流程。

### 不是 `CaiTH0618` 相关仓库的 Collaborator

先在自己的 GitHub 账号中 fork Chipyard，以及本次需要修改的每个 submodule。将原始远端保留为 `upstream`，并把 `origin` 指向自己的 fork；将 `<username>` 替换为自己的 GitHub 用户名：

```bash
git remote rename origin upstream
git remote add origin git@github.com:<username>/chipyard.git
git switch -c npu/<topic>
git push -u origin npu/<topic>
```

以 Rocket Chip 为例，对需要修改的 submodule 执行：

```bash
git -C generators/rocket-chip remote rename origin upstream
git -C generators/rocket-chip remote add origin git@github.com:<username>/rocket-chip.git
git -C generators/rocket-chip switch -c npu/<topic>
git -C generators/rocket-chip push -u origin npu/<topic>
```

Gemmini、`gemmini-rocc-tests`、ActiveSPM 和 FireSim 使用相同流程，并替换为相应的路径和个人仓库 URL。

非 Collaborator 的 submodule commit 只存在于个人 fork，因此还必须在个人功能分支中修改父仓库的 `.gitmodules`，让其指向个人 fork 的 HTTPS URL。例如修改 Rocket Chip 时，在顶层 Chipyard 执行：

```bash
git config --file .gitmodules submodule.generators/rocket-chip.url https://github.com/<username>/rocket-chip.git
```

如果修改的是 `gemmini-rocc-tests`，则修改 `generators/gemmini/.gitmodules` 中对应的 URL。这样其他人 checkout 个人功能分支时，才能获取该分支记录的 submodule commit。

个人 URL 只保留在个人功能分支中。改动准备合入 `CaiTH0618` 仓库时，相关 submodule commit 必须先进入对应的 `CaiTH0618` 仓库，并将 `.gitmodules` 恢复为 `CaiTH0618` URL。

## 提交跨仓库修改

始终先提交并 push 最内层仓库，再逐级更新父仓库记录的 submodule commit。例如：

```text
gemmini-rocc-tests
→ gemmini
→ chipyard
```

确认子模块 commit 已经 push 到父仓库 `.gitmodules` 所记录的远端后，才能在父仓库提交对应路径。如果修改过 `.gitmodules`，应与 submodule gitlink 一起提交：

```bash
git add .gitmodules generators/gemmini
git commit
git push origin npu/<topic>
```

这样其他开发者才能从远端获取父仓库所记录的完整版本。
