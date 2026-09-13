# Documents

## Introduction

The docs explain the customization work on the original Chipyard. Customized modules are at local branch `npu/*` and remote branch `origin/npu/*`.Customization involves: 
- `.`
- `./generators/rocket-chip`
- `./generators/gemmini`
- `./generators/gemmini/software/gemmini-rocc-tests`
- `./generators/activespm`
- `./sims/firesim`

NOTE: These modules have different upstream url than the orignal Chipyard. But the .gitmodules still save the original url for submodules. So `git submodule xxx` might override their upstream url.

## Principles

The docs are regulated by the following principles: 
- Written in English.
- Only focus on the customization parts and the original parts deeply related to the customization.
- Only explain the functionality, interface, interaction, connection and usage. 
- No detailed information on implementation details for the easy-to-update of the docs.
- No direct reference to specific lines of code for the easy-to-update of the docs. 

## Index

This section gives index and brief introduction to other doc files.

- `docs-npu/path-to-file/file-name.md`: Some brief introduction. 

