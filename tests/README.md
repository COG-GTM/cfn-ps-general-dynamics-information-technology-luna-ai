# Handler tests

Unit tests for the Lambda handlers in `src/handlers/`. Every AWS call is mocked:
S3, DynamoDB and EventBridge by [moto](https://github.com/getmoto/moto), and
Textract, Comprehend and Rekognition by `botocore.stub.Stubber`, which also
validates each request against the service model. No AWS account, credentials
or network access is needed; `--disable-socket` fails any test that opens a
real connection.

```
python -m pip install -r requirements-dev.txt
python -m pytest --cov --cov-report=term-missing
```

Tests marked "Characterization" in their docstring pin down existing behaviour,
including defects, so that later changes to it are deliberate.
