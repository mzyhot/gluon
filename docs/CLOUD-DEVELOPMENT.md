# Cloud development

Run macOS builds and runtime checks in GitHub Actions. Codespaces is optional for
source review and Linux source validation. Local Mac work normally consists of
reading/editing files and controlling GitHub through its API or CLI. Compilation
and full runtime builds stay on Actions. The owner's latest instruction permits
one explicitly authorized short local test at a time, with reduced workload and
priority, a hard timeout, stop on failure and prefix-specific cleanup. Never
repeat the failed partial OpenGL-module substitution or run parallel local probes.
Temperature polling was withdrawn; do not start it without a new request.

## Codespaces

If a remote editor is useful, create a Codespace from the development branch and select the smallest available
machine (the configuration needs 2 CPUs and 4 GB of memory). Use the browser editor
or `gh codespace ssh` to execute commands remotely. The checked-in devcontainer
provides Python and source inspection tools; starting it only provisions those
tools and does not run tests, build a runtime, or launch a game.

Run this command **inside the Codespace terminal**:

```sh
timeout 19m bash packaging/check-sources.sh
```

This checks tracked shell/Python syntax, downloads the same SHA-256-pinned
CodeWeavers open-source archive as the runtime build, and extracts only the
Wine/DXVK source files needed for validation. It applies the Wine alias patch,
verifies repeat application, checks all 38 aliases and their signatures against
the pinned Khronos XML registry, runs the validator's negative cases, and checks
all three DXVK patches. The helper rejects macOS and environments outside GitHub
Codespaces or GitHub Actions. Download cache contents are reverified on every run.

On GitHub Actions Linux, the checks also compile the exact patched Wine
`extract_exe_name` function as an isolated string fixture, test executable suffixes,
paths and unchanged input, and verify that the fixture rejects the original bug.
The helper compilation has a 20-second deadline and each fixture a five-second
deadline. It links no Wine modules and starts no Wine process. Codespaces runs
only its source-contract check; helper compilation stays on Actions.

The same bounds apply to an isolated fixture of the exact Wine function resolver.
It checks WGL dispatch separately from GL extensions, active-context requirements,
cached GL capability changes and valid registry index zero. Restoring the faulty
unconditional GL gate must fail all three WGL lookup cases. This fixture uses
controlled dispatch slots and calls no graphics API.

The opt-in macdrv compatibility-as-core decision helper is compiled the same
way and checked against 24 switch/version/profile/flag cases. Mutations that
ignore `GLUON_GL_COMPAT_AS_CORE` or remap already supported requests must fail.
Codespaces runs only its source contract.

The app-bundle fixture compiles Wine's exact bundled-loader check against a
stubbed executable-path lookup: loaders inside `.app/Contents/MacOS` are kept,
temporary links and look-alike paths are not; the first process enters the
game directory passed by the launcher once and clears it. The overlay-scope fixture runs the
exact patched functions to check that only game programs receive Steam's overlay
and that the server's environment never does; giving the overlay to every
program or to the server must fail.

No native graphics API runs during these checks. Linux Codespaces cannot verify Apple's Metal/OpenGL behavior, Rosetta, macOS WGL contexts, or game
rendering. A passing source check therefore establishes source consistency only.

Stop the Codespace when finished to stop its compute usage:

```sh
gh codespace stop -c <codespace-name>
```

GitHub supports [minimum Codespaces machine requirements](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/configuring-dev-containers/setting-a-minimum-specification-for-codespace-machines)
and the [devcontainer configuration](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/adding-a-dev-container-configuration/introduction-to-dev-containers)
used here. Machine requirements are minimums; they do not cap resource usage or
set an idle timeout. Set a short Codespaces idle timeout when creating the session.

## GitHub Actions

The **Source checks** workflow runs on pull requests, pushes to `main` and
`codex/**`, and manual dispatch. It uses a GitHub-hosted Ubuntu runner, read-only
repository permissions, a 20-minute job timeout, and cancellation of older source
check runs for the same branch or pull request. It uploads the text check log for
seven days, including failures. Its download cache is separate from runtime build
outputs.

Dispatch it against a committed and pushed branch:

```sh
gh workflow run source-checks.yml --ref <branch>
gh run list --workflow source-checks.yml --branch <branch> --limit 5
gh run view <run-id> --log-failed
```

The separate **Build** workflow builds and packages the macOS runtime on a
GitHub-hosted macOS runner. Runtime build results and compile-only probe evidence
belong to that workflow. Check its logs before interpreting a package as ready;
source checks do not replace a successful macOS build or rendering validation.

Before the runtime build, a separate macOS job compiles a universal native
launcher host and checks Cocoa quit delivery, inherited signal state, argument
and status propagation, independent concurrent hosts, and Steam quit through a
controlled Steam client library and fake Steam application. It uses harmless
fixtures under a 60-second deadline; no Steam, Wine or game runs in that job. Packaging
requires that tested host and installs it beside the shell launcher.

The macOS job additionally runs each complete-runtime probe with a 30-second
deadline (60 seconds for first prefix setup), uses one disposable CI prefix and
stops its server afterward. Module/loader checks can pass without a GPU; missing
native CGL or Metal capability is reported as a skipped rendering prerequisite.
Timeouts and failed assertions fail the job. Logs and `results.json` are uploaded.

Completed runtimes and compiled probes are preserved for three days before runtime
checks start. A failed check can then be retried on a fresh cloud runner without
recompiling. Dispatch the same Build workflow with `recheck_run_id=<build-run-id>`
and a package version. Rechecks verify the originating repository, archive digest,
runtime patch/build stamps and compiled-probe source hashes before running the
bounded checks. Packaging still requires success, and rechecks never publish a
release. The harness waits for prefix setup to become idle before loading probe
DLLs; unbuffered Vulkan stage logs identify where a later timeout occurs.

GitHub documents the [workflow timeouts and concurrency controls](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
used to bound cloud runs. Workflow dispatch and monitoring via `gh` run locally as
API requests; the workflow jobs themselves run on GitHub's machines.
