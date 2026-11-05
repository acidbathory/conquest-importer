# New Relic metric names, checked against real AWS data

Checked on 2026-11-05 by creating the services in an AWS test account, streaming their metrics into
a New Relic test account with the importer's own generated Metric Streams Terraform, converting one
alarm per metric with the importer, and comparing each converted query with CloudWatch's own
numbers (`importer validate`, 5-minute window, 2% tolerance). The services ran for about two
and a half hours at roughly $0.15 an hour and were destroyed afterwards.

## Result

| Service | Metrics compared | New Relic name | Dimension attributes | Result |
|---|---|---|---|---|
| RDS | CPU, freeable memory, connections, free storage | `aws.rds.<Metric>` | `aws.rds.DBInstanceIdentifier` | Match |
| ElastiCache | CPU, current connections, freeable memory | `aws.elasticache.<Metric>` | `aws.elasticache.CacheClusterId` | Match |
| EBS | Read ops, write ops, idle time | `aws.ebs.<Metric>` | `aws.ebs.VolumeId` | Match |
| Network Load Balancer | Active flows, new flows, processed bytes | `aws.networkelb.<Metric>` | `aws.networkelb.LoadBalancer` | Match |
| Classic ELB | 5xx count | `aws.elb.<Metric>` | `aws.elb.LoadBalancerName` | Match |
| S3 request metrics | All requests, GET requests | `aws.s3.<Metric>` | `aws.s3.BucketName`, `aws.s3.FilterId` | Match |
| ECS service | CPU, memory | `aws.ecs.<Metric>.byService` | `aws.ecs.ClusterName`, `aws.ecs.ServiceName` | Match after the fix below |

## What it found

- **ECS service metrics carry a `.byService` suffix.** The importer wrote `aws.ecs.CPUUtilization`,
  which returned nothing. It now writes `aws.ecs.CPUUtilization.byService` for alarms with both
  `ClusterName` and `ServiceName`, and the comparison matches (0 and 0.585938 on both sides).
- Every other service used the plain `aws.<service>.<Metric>` name with no suffix.

## What this does not cover

- **Classic ELB `RequestCount` and `Latency`.** CloudWatch publishes them only when a healthy
  backend answers, and the test instance ran no web server, so neither side had data.
- **S3 storage metrics** (`BucketSizeBytes`, `NumberOfObjects`) arrive once a day, outside the test window.
- **ECS metrics for a cluster** (not a service) need EC2 capacity and were not created.
- **Sums need a settled window.** Compared a minute after a window closed, sums were low (for
  example classic ELB 5xx showed 20 against 27) because the stream and CloudWatch lag by several
  minutes. A few minutes later they matched exactly. Use a window that ended at least ten minutes
  earlier, as `importer validate` recommends.
- Another metric stream already existed in the test account. It was left alone and did not change
  any of the numbers above.
