# Inert upstream workflows

These files preserve the inherited `nix-darwin/nix-darwin` workflows for
ancestry review. Their `.disabled` suffix and location outside
`.github/workflows/` make them invisible to GitHub Actions.

They must not be renamed or copied back into `.github/workflows/` in the
AxiomLayer fork. The test snapshot installs and activates system software; the
website snapshot builds and deploys GitHub Pages with write and OIDC authority.
Only `.github/workflows/axiomlayer-integration.yml` is executable in this fork.
