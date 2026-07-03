# Supply Chain Security

PortraitHub release evidence should cover source, dependencies, container image, and model artifacts.

Required controls:

- Generate a CycloneDX SBOM for the runtime container.
- Scan the container image with Trivy.
- Run `pip-audit` against locked dependency manifests.
- Publish SLSA provenance or equivalent signed provenance for release artifacts.
- Sign release images with cosign.
- Keep OSSF Scorecard in CI.
- Use Dependabot or Renovate for pinned dependency updates.

Model artifact controls:

- Every production model should have a pinned `artifact.sha256`.
- Every production model should have a model card and a governance sidecar.
- Hashes and cards must be checked before alias cutover.
