# Contributing to Rintel Core

Thank you for helping improve Rintel. This repository is a local-first code-evidence product. Keep design claims, static evidence, runtime observations, and human annotations separate; a faster or prettier result must not silently change canonical truth.

Before opening a pull request, search existing issues and describe the problem, intended scope, and evidence. For a code change, include a focused regression test and run the relevant backend tests plus `pnpm --dir web test`, `pnpm --dir web typecheck`, and `pnpm --dir web build` when the UI is affected. Use `./install.sh` for the supported local installation path. Do not commit databases, generated indexes, credentials, browser profiles, or user repositories.

Submit changes through a GitHub pull request against `main`. The staged Guided Mission work is the exception: component and map PRs target `guided-mission/integration`, and only its accepted integration PR targets `main`; see [AGENTS.md](AGENTS.md) for the branch and evidence gates. By submitting a contribution, you agree that you have the right to submit it under Apache-2.0. Security issues should follow [SECURITY.md](SECURITY.md), not a public issue with exploit details. The maintainers may ask for narrower scope or more evidence before merging.
