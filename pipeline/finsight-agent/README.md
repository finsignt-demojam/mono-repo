# FinSight Demo Pipeline

This directory contains the FinSight audio analytics demo:

- `app/` – Knative event handler that reacts to MinIO uploads and launches Kubeflow Pipelines.
- `pipeline/` – Kubeflow v2 component definitions and the compiled pipeline entry point.
- `audio_pipeline.yaml` – Precompiled DSL artifact for manual uploads if needed.
- `scripts/` – Helper utilities (for example the MinIO upload smoke-test script).
- `docs/` – Operational runbooks, including the MinIO ↔ Kafka integration notes.

These artifacts are consumed by the GitOps manifests under `components/platform`, so updates here should stay in lockstep with the Kubernetes resources.
