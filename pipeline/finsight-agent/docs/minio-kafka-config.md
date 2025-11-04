# MinIO ↔ Kafka Integration

This note captures the configuration enforced by GitOps for wiring MinIO object events into the Kafka/Knative pipeline.

## Notification Targets

The PostSync job (`components/platform/finsight-eventing/base/configure-minio-kafka-job.yaml`) provisions two MinIO notify targets:

| Alias | Topic | Brokers |
|-------|-------|---------|
| `FINSIGHT` | `minio-bucket-notifications` | `finsight-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092` |
| `EVENTS` | `minio-bucket-notifications` | `finsight-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092` |

Events from the `audio-inbox` bucket are re-bound to the `EVENTS` alias:

```shell
mc event remove finsight/audio-inbox --force
mc event add finsight/audio-inbox arn:minio:sqs::EVENTS:kafka --event put
```

The job restarts MinIO (`mc admin service restart finsight --json`) so the updated configuration takes effect.

## Secrets

- `finsight-agent/minio-credentials` – used by the Knative service.
- `minio/minio-credentials` – used by the PostSync job to call the admin API.

Both carry `accesskey=minio-root` and `secretkey=CrazyHorse` for the demo environment.

## KafkaSource Parameters

```
bootstrapServers: [finsight-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092]
consumerGroup: finsight-pipeline-e2e
initialOffset: latest
```

Bumping the consumer group name is a quick way to drop historic events if the topic grows too large.

## Namespace Annotation

`components/platform/finsight-namespaces` enables `eventing.knative.dev/injection: enabled` on the `finsight-agent` namespace so a Kafka-backed `default` broker is created automatically. Apply the same annotation if you move the workloads to another namespace.
