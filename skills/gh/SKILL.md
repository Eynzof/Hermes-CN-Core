---
name: gh
description: GitHub CLI (gh) installation and usage guide covering Windows, Linux, and macOS. Use when the user asks how to install, update, authenticate, or use the GitHub CLI (gh) — including package managers (winget, Homebrew, apt, dnf, pacman), gh auth login, and common commands for repos, PRs, issues, Actions, and releases.
---

# GitHub CLI (gh)

`gh` is GitHub's official command line tool. It brings pull requests, issues, Actions, releases, and the GitHub API to the terminal, next to where `git` and your code already live. Works on GitHub.com, GitHub Enterprise Cloud, and GitHub Enterprise Server 2.20+.

Prerequisite: `git` must be installed separately (`git --version`).

Official docs: <https://cli.github.com/manual/> · Repo: <https://github.com/cli/cli>

## Install

### Windows (official method: WinGet)

```powershell
winget install --id GitHub.cli --source winget
```

The installer modifies PATH — open a **new terminal window** afterward (a new tab is not enough). Alternatives: `choco install gh`, `scoop install gh`, or the MSI from <https://cli.github.com/>.

Upgrade: `winget upgrade --id GitHub.cli` · Uninstall: `winget uninstall GitHub.cli`

### macOS (official method: Homebrew)

```bash
brew install gh
```

Handles Apple Silicon and Intel; shell completions included. Alternative: `sudo port install gh`. Precompiled binaries are on the releases page.

Upgrade: `brew upgrade gh` · Uninstall: `brew uninstall gh`

### Linux

Add GitHub's signed package repository, then install (distro repos often ship a version years behind).

**Debian / Ubuntu / Raspberry Pi OS:**

```bash
(type -p wget >/dev/null || (sudo apt update && sudo apt install wget -y)) \
  && sudo mkdir -p -m 755 /etc/apt/keyrings \
  && wget -nv -O- https://cli.github.com/packages/githubcli-archive-keyring.gpg \
     | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
  && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
     | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && sudo apt update && sudo apt install gh -y
```

The `chmod go+r` on the keyring is required — otherwise apt fails with `NO_PUBKEY`. Note: GitHub rotated the Linux package signing key effective September 5, 2026; if apt suddenly reports key errors on an old setup, re-run the keyring commands above.

**Fedora / RHEL / CentOS / Amazon Linux (dnf):**

```bash
sudo dnf install 'dnf-command(config-manager)'
sudo dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo
sudo dnf install gh
```

Do not restrict the transaction to `--repo gh-cli` — `gh` depends on `git`, which must come from the distro repos.

**openSUSE / SUSE:** `sudo zypper addrepo https://cli.github.com/packages/rpm/gh-cli.repo && sudo zypper ref && sudo zypper install gh`

**Arch:** `sudo pacman -S github-cli` (community repo)

Other options: Homebrew on Linux, Conda (`conda install gh --channel conda-forge`), or binaries from the releases page. Avoid Snap — GitHub does not recommend it.

### Verify

```bash
gh --version   # expect e.g. "gh version 2.96.0 (2026-07-02)"
```

Verify a downloaded binary's provenance with `gh at verify -R cli/cli <file>` (requires an existing gh) or Sigstore `cosign`. Releases have carried Build Provenance Attestation since v2.50.0 and are immutable since v2.93.0.

## Authenticate

```bash
gh auth login                    # interactive: pick github.com, HTTPS/SSH, browser or token
gh auth status                   # confirm active account and scopes
gh auth token                    # print the active token
gh auth setup-git                # use gh as git credential helper
gh auth refresh --scopes workflow  # add scopes
gh auth switch --user <name>     # switch between accounts
gh auth logout                   # remove credentials
```

For automation or CI, skip the browser flow: `gh auth login --with-token < token.txt`, or set the `GH_TOKEN` (preferred) / `GITHUB_TOKEN` environment variable. Choose minimum scopes: `repo` for private repos, `read:org` for org repos, `workflow` for Actions files.

GitHub Enterprise: `gh auth login --hostname <host>`, then set `GH_HOST=<host>` and `GH_ENTERPRISE_TOKEN=<token>` as needed.

## Core usage

Most commands run inside a local git repo and infer `OWNER/REPO` from `git remote`.

```bash
# Repos
gh repo create my-app --public --source=. --push   # create + push current dir
gh repo clone owner/repo                           # clone
gh repo fork owner/repo --clone                    # fork and clone
gh repo view --web                                 # open repo in browser (-w)

# Pull requests
gh pr status                 # PRs relevant to current branch
gh pr create --fill          # open a PR, title/body from commits
gh pr list --assignee @me    # list PRs
gh pr checkout 101           # check out PR #101 locally
gh pr diff 101               # view diff
gh pr review --approve 101   # review
gh pr merge 101 --squash     # merge (--auto, --delete-branch)

# Issues
gh issue create --title "Bug" --body "Details" -a @me -l bug
gh issue list --state open
gh issue status
gh issue close 42 --comment "Fixed in v1.2"

# GitHub Actions
gh run list                  # recent workflow runs
gh run view <id> --log-failed   # why did it fail
gh run watch                 # live-follow current run
gh workflow run ci.yml
gh workflow list

# Releases & gists
gh release create v1.0.0 dist/*.zip --title "v1.0" --notes "..."
gh release list / gh release view v1.0.0
gh gist create file.py --public

# Search & API (scriptable)
gh search repos "topic:machine-learning" --limit 10
gh search issues "is:open label:bug" --repo owner/repo
gh api repos/owner/repo/pulls --jq '.[].title'   # jq placeholders built in
gh api graphql -f query='query { viewer { login } }'
```

Productivity: `gh alias set pv 'pr view --web'`, `gh config set editor vim`, `gh extension install <owner/repo>`. Enable completions: `gh completion -s bash|zsh|fish|powershell` (add to shell rc). Get help: `gh help`, `gh <cmd> --help`, or the online manual.

## Troubleshooting

- **`gh: command not found` (Windows)**: installer updated PATH — open a new terminal window.
- **apt `NO_PUBKEY` / signature errors**: keyring not world-readable (`chmod go+r`) or key rotation after Sep 5, 2026 — re-import the keyring above.
- **Old version on Linux**: the distro's repo lags upstream; use GitHub's own apt/dnf repo, Homebrew, or release binaries.
- **`nothing provides git needed by gh` (dnf)**: install git first, or drop `--repo gh-cli` so dnf resolves `git` from distro repos.
- **Auth failures in scripts**: ensure `GH_TOKEN`/`GITHUB_TOKEN` is set and scopes are sufficient (`gh auth status` to check).
