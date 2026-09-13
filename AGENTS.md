Project Introduction
- This is a customized Chipyard project.
- Customized modules are at local branch `npu/*` and remote branch `origin/npu/*`.
- Customization involves: 
  - `.`
  - `./generators/rocket-chip`
  - `./generators/gemmini`
  - `./generators/gemmini/software/gemmini-rocc-tests`
  - `./generators/activespm`
  - `./sims/firesim`
- Documents on customization are in `./docs-npu`. Entry document is `./docs-npu/index.md`

Project Usage
- Run `source env.sh && source scripts/chipyard-build-resources.sh` to enter the Chipyard environment.
