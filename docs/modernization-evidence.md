# Modernization evidence

Baseline versus after for the assessment, test backfill, runtime upgrade and
security fixes made to this repository. Every number below was measured by the
commands in [Reproduce](#reproduce); raw scanner outputs are kept outside the
repository.

**Nothing was deployed.** No AWS account or credentials were used. Verification
is limited to mocked unit tests, cfn-lint template validation and static
scanners. Scanner results describe what the tools report; they are not a
statement about any control or standard.

- Baseline commit: `3c49020` (`main`)
- Tools: cfn-lint 1.60.1, cfn_nag 0.8.10, Checkov 3.4.6, Bandit 1.9.4, pip-audit 2.10.1
- Both `template.yaml` and `templates/packaged.yaml` were scanned and gave identical counts, so the tables show counts **per template**.

## Runtime, tests and coverage

| | Baseline | After |
|---|---|---|
| Lambda runtime in templates | `python3.8` (55 occurrences across both templates) | `python3.12` (0 occurrences of `python3.8`) |
| Unit tests | 0 | 37 passed, 0 failed |
| Coverage of `src/handlers` (statement + branch) | 0% (no tests) | 94% (1250 statements, 54 missed) |
| Interpreter used for tests | none | CPython 3.8.20 (before upgrade) and 3.12.13 (after) |
| CI | none | GitHub Actions workflow `tests` runs the suite on every pull request |

AWS is fully mocked in the tests (moto for S3, DynamoDB and EventBridge;
`botocore` Stubber for Textract, Comprehend and Rekognition) and sockets are
disabled by `pytest-socket`.

## Scanner findings (per template)

| Tool | Severity | Baseline | After | Change |
|---|---|---:|---:|---:|
| cfn-lint | Error | 21 | 21 | 0 |
| cfn-lint | Warning | 48 | 21 | -27 |
| cfn_nag | FAIL | 6 | 0 | -6 |
| cfn_nag | WARN | 124 | 110 | -14 |
| Checkov | failed checks (Checkov reports no severity) | 168 | 133 | -35 |
| Bandit (`src/`, 2018 lines) | all | 0 | 0 | 0 |
| pip-audit (`requirements-dev.txt`, 30 resolved packages) | packages with advisories | 1 | 0 | -1 |

The repository has no runtime dependency file; the handlers use only the
standard library and the boto3 provided by Lambda. pip-audit was therefore run
against the test-only requirements, which were added with the test suite.

### What was fixed

| Finding | Rules | Baseline | After | Fix |
|---|---|---:|---:|---|
| SQS queue policies allowed any principal to perform any SQS action | cfn_nag `F20`, `F21` | 6 | 0 | Policies now allow only `s3.amazonaws.com` to `sqs:SendMessage`, and only for buckets in the same account (`aws:SourceAccount`) |
| End-of-life Lambda runtime | cfn-lint `W2531` | 27 | 0 | `python3.8` to `python3.12` |
| SNS topics not encrypted at rest | cfn_nag `W47`, Checkov `CKV_AWS_26` | 3 + 3 | 0 | `KmsMasterKeyId: alias/aws/sns` on the three topics |
| DynamoDB tables without point-in-time recovery | cfn_nag `W78`, Checkov `CKV_AWS_28` | 11 + 11 | 0 | `PointInTimeRecoveryEnabled: true` on the 11 tables |
| S3 buckets without versioning | Checkov `CKV_AWS_21` | 21 | 0 | `VersioningConfiguration: Enabled` on the 21 buckets |
| Advisory in a test-only dependency | pip-audit `PYSEC-2026-1845` (pytest 8.4.2) | 1 | 0 | `pytest>=9.0.3` |

SQS queues now set `SqsManagedSseEnabled: true` (SSE-SQS). cfn_nag `W48` and
Checkov `CKV_AWS_27` only recognise a KMS key, so they still report the three
queues. A KMS key was not used because the queues receive S3 event
notifications, which would need a customer-managed key and key policy that
could not be exercised without a deployment.

### What remains (counts only, per template)

| Tool | Rule | Count |
|---|---|---:|
| cfn-lint | `E3045` (Error) | 21 |
| cfn-lint | `W3045` (Warning) | 21 |
| cfn_nag | `W35` | 21 |
| cfn_nag | `W48` | 3 |
| cfn_nag | `W51` | 21 |
| cfn_nag | `W74` | 11 |
| cfn_nag | `W89` | 27 |
| cfn_nag | `W92` | 27 |
| Checkov | `CKV_AWS_115` | 27 |
| Checkov | `CKV_AWS_116` | 27 |
| Checkov | `CKV_AWS_117` | 27 |
| Checkov | `CKV_AWS_119` | 11 |
| Checkov | `CKV_AWS_173` | 17 |
| Checkov | `CKV_AWS_18` | 21 |
| Checkov | `CKV_AWS_27` | 3 |

## Not verified

- No stack was deployed, so the template changes were not exercised against
  real services. In particular: S3-to-SQS notification delivery under the
  narrowed queue policies, and Textract/Rekognition publishing to the
  now-encrypted SNS topics, are untested.
- The handlers were run on CPython 3.12.13 locally and in CI, not on the Lambda
  `python3.12` runtime itself.
- `template.yaml` references 12 handler files that are not in the repository.
  They have no tests and are outside the coverage figure.
- Enabling S3 versioning keeps noncurrent object versions; no lifecycle rule
  was added, so storage growth and stack deletion behaviour were not assessed.

## Reproduce

```bash
# tools
python3 -m venv .tools && .tools/bin/pip install cfn-lint==1.60.1 checkov==3.4.6 bandit==1.9.4 pip-audit==2.10.1
gem install cfn-nag -v 0.8.10

# scanners (run on the baseline commit and again on this branch)
for t in template.yaml templates/packaged.yaml; do
  .tools/bin/cfn-lint -f json "$t"
  cfn_nag_scan --input-path "$t" --output-format json
done
.tools/bin/checkov -f template.yaml -f templates/packaged.yaml --framework cloudformation -o json --quiet
.tools/bin/bandit -r src -f json
.tools/bin/pip-audit -r requirements-dev.txt

# tests and coverage (no AWS account, credentials or network needed)
python3.12 -m pip install -r requirements-dev.txt
python3.12 -m pytest --cov --cov-report=term-missing
```
