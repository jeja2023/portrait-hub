# Privacy And Biometric Compliance

PortraitHub deployments should be treated as biometric-adjacent systems.

Required controls:

- Record consent or another lawful basis before enrollment or identification use.
- Keep purpose limitation explicit per tenant.
- Configure retention and deletion paths.
- Encrypt sensitive state at rest.
- Redact embeddings, vectors, secrets, filenames, and raw stream URLs from public responses and logs.
- Keep human review available for high-impact decisions.
