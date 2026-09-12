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
- Run `source env.sh` to enter the Chipyard environment.

Rules for Agents:
- Update `./docs-npu` after modification if it's necessary. Follow the docs principles in `./docs-npu/index.md`

