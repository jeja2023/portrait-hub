# AI Model Governance

Production model releases must carry a model card and a governance sidecar.

Required sidecar sections:

- `dataset_lineage`
- `bias`
- `threshold_calibration`
- `risk_management`
- `human_review`
- `drift_monitoring`
- `privacy`
- `release`

Release gates:

1. Validate the model package with governance checks before cutover.
2. Verify regression gates on held-out samples.
3. Require an explicit rollback target for every active alias.
4. Review ambiguous scores in a human review band.
5. Track drift and threshold recalibration as a release artifact, not a note.
