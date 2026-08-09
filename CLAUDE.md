# Working in this repo

## PR titles (CI-enforced)

PR titles are linted by `.github/workflows/pr-title.yml` and **must** be
Conventional Commits **with a scope** — a bare `feat: ...` fails the check:

```
type(scope): summary        e.g.  feat(apps): bus app — live next-bus times…
```

- Types: feat, fix, refactor, perf, docs, test, build, ci, chore, revert.
- Scopes: kernel, apps, tools, tooling, cad, dev, fonts, docs, ci
  (`release` is reserved for release-please).
- Pick the scope from what the change mostly touches. A new app is
  `feat(apps): <name> app — <what it shows>`.

Squash-merge titles become the commits on main and drive release-please's
version and CHANGELOG — get the title right **before** `gh pr create`.

## Git flow

PRs go from the `claireorourke` fork (`origin`) to `ibennet/dizzyos` main
(`upstream`). Base new work on `upstream/main` — the local checkout and
`origin/main` lag behind it.

## Before pushing

Run the green gate; CI runs exactly this:

```bash
./dev/check.sh
```
