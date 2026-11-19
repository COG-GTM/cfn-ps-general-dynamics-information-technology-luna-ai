"""Shared fixtures: every AWS call is served by moto or a botocore Stubber.

No AWS account, credentials or network access is used. ``--disable-socket``
(pytest.ini) makes any real network attempt fail the test.
"""
import importlib
import json
import os
import sys
from unittest import mock

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

HANDLERS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "handlers")
REGION = "us-east-1"
ACCOUNT = "123456789012"
ASSET_ID = "0123456789abcdef0123456789abcdef"
EVENT_BUS = "me-bus"
INGEST_BUCKET = "test-ingest"

BUCKET_ENV = [
    "S3txtract", "S3txtractA", "S3txtPlain", "S3NlpTop", "S3NlpNer", "S3NlpLang",
    "S3NlpKp", "S3Assets", "S3Kendra", "S3Translate", "S3Transcribe", "S3RkTxtDet",
    "S3RkSegDet", "S3RkPplTrc", "S3RkLblDet", "S3RkFacSrch", "S3RkFacDet",
    "S3RkCeleb", "S3RkMod", "S3AzureDescrImg",
]

# env var -> (hash key, range key, global secondary indexes); mirrors template.yaml
TABLES = {
    "AssetTable": (("AssetId", "S"), None, []),
    "AssetProcessingTable": (("AssetId", "S"), ("ProcessId", "S"), []),
    "AssetAttributeTable": (("AssetId", "S"), ("AttributeType", "S"), []),
    "AssetEnrichmentsTable": (("AssetId", "S"), ("Timestamp", "N"), []),
    "AssetHistoryTable": (("AssetId", "S"), None, []),
    "AssetFormatsTable": (("AssetId", "S"), ("FormatType", "S"), []),
    "ErrorTable": (("AssetId", "S"), ("Source", "S"), []),
    "SearchTable": (
        ("Term", "S"), ("Context", "S"),
        [("AssetIdTerm", ("AssetId", "S"), ("Term", "S")),
         ("TermConfidence", ("Term", "S"), ("Confidence", "N"))],
    ),
    "SearchAggregateTable": (
        ("AssetId", "S"), ("ProcessType", "S"),
        [("ContextAssetId", ("Context", "S"), ("AssetId", "S"))],
    ),
}


def _environment():
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_EC2_METADATA_DISABLED": "true",
        "EventBus_Name": EVENT_BUS,
        "rkConfidence": "80",
        "ingestQueueUrl": "https://sqs.%s.amazonaws.com/%s/ingest" % (REGION, ACCOUNT),
        "ComprehendAccessarn": "arn:aws:iam::%s:role/ComprehendAccess" % ACCOUNT,
        "TextractSNSarn": "arn:aws:iam::%s:role/TextractSNSRl" % ACCOUNT,
        "TextractSNSTopicArn": "arn:aws:sns:%s:%s:AmazonTextractSNS" % (REGION, ACCOUNT),
        "TextractASNSTopicArn": "arn:aws:sns:%s:%s:AmazonTextractASNS" % (REGION, ACCOUNT),
        "vid_rkfacedetectSNSarn": "arn:aws:sns:%s:%s:AmazonRkFaceDetSNS" % (REGION, ACCOUNT),
        "vid_rkfacedetectRolearn": "arn:aws:iam::%s:role/RekognitionAccess" % ACCOUNT,
    }
    for name in BUCKET_ENV:
        env[name] = "test-" + name.lower()
        env[name + "arn"] = "arn:aws:s3:::test-" + name.lower()
    for name in TABLES:
        env[name] = "test-" + name
    return env


def _create_table(client, name, hash_key, range_key, indexes):
    attrs = dict([hash_key] + ([range_key] if range_key else []))
    schema = [{"AttributeName": hash_key[0], "KeyType": "HASH"}]
    if range_key:
        schema.append({"AttributeName": range_key[0], "KeyType": "RANGE"})
    kwargs = {}
    gsis = []
    for index_name, index_hash, index_range in indexes:
        attrs.update(dict([index_hash, index_range]))
        gsis.append({
            "IndexName": index_name,
            "KeySchema": [
                {"AttributeName": index_hash[0], "KeyType": "HASH"},
                {"AttributeName": index_range[0], "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        })
    if gsis:
        kwargs["GlobalSecondaryIndexes"] = gsis
    client.create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": k, "AttributeType": v} for k, v in attrs.items()],
        KeySchema=schema,
        BillingMode="PAY_PER_REQUEST",
        **kwargs
    )


class Aws:
    """Handles to the mocked account used for arranging and asserting."""

    def __init__(self, env):
        self.env = env
        self.s3 = boto3.client("s3")
        self.dynamodb = boto3.resource("dynamodb")

    def table(self, env_name):
        return self.dynamodb.Table(self.env[env_name])

    def items(self, env_name):
        return self.table(env_name).scan()["Items"]

    def put(self, bucket_env, key, body=b"x"):
        bucket = self.env.get(bucket_env, bucket_env)
        self.s3.put_object(Bucket=bucket, Key=key, Body=body)

    def keys(self, bucket_env):
        bucket = self.env.get(bucket_env, bucket_env)
        listing = self.s3.list_objects_v2(Bucket=bucket)
        return sorted(o["Key"] for o in listing.get("Contents", []))

    def read(self, bucket_env, key):
        bucket = self.env.get(bucket_env, bucket_env)
        return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()


@pytest.fixture
def aws():
    env = _environment()
    with mock.patch.dict(os.environ, env):
        with mock_aws():
            s3 = boto3.client("s3")
            for name in BUCKET_ENV:
                s3.create_bucket(Bucket=env[name])
            s3.create_bucket(Bucket=INGEST_BUCKET)
            dynamodb = boto3.client("dynamodb")
            for name, (hash_key, range_key, indexes) in TABLES.items():
                _create_table(dynamodb, env[name], hash_key, range_key, indexes)
            boto3.client("events").create_event_bus(Name=EVENT_BUS)
            yield Aws(env)


@pytest.fixture
def load_handler(aws):
    """Import a handler module fresh, inside the mocked account.

    Handlers create boto3 clients and read os.environ at import time, so each
    test gets its own import. EventBridge ``put_events`` is wrapped in a spy.
    """
    sys.path.insert(0, HANDLERS_DIR)
    loaded = []

    def _load(name):
        sys.modules.pop(name, None)
        module = importlib.import_module(name)
        loaded.append(name)
        module.clientEvents.put_events = mock.Mock(wraps=module.clientEvents.put_events)
        return module

    yield _load
    for name in loaded:
        sys.modules.pop(name, None)
    sys.path.remove(HANDLERS_DIR)


@pytest.fixture
def stub():
    """Stub a boto3 client for a service moto is not used for (AI services)."""
    stubbers = []

    def _stub(client):
        stubber = Stubber(client)
        stubber.activate()
        stubbers.append(stubber)
        return stubber

    yield _stub
    for stubber in stubbers:
        stubber.assert_no_pending_responses()
        stubber.deactivate()


def published_events(module):
    """Entries sent to EventBridge by a handler, with Detail decoded."""
    entries = []
    for call in module.clientEvents.put_events.call_args_list:
        for entry in call.kwargs["Entries"]:
            decoded = dict(entry)
            decoded["Detail"] = json.loads(entry["Detail"])
            entries.append(decoded)
    return entries


def ingestion_event(detail_type="ingestion", **detail):
    """EventBridge event in the shape the ingestion handler publishes."""
    base = {
        "AssetId": ASSET_ID,
        "AssetDatabase": "demodb",
        "AssetSize": 1024,
        "AssetType": "pdf",
        "AssetStorage": "store",
        "AssetStorageBucket": "test-s3assets",
        "AssetStorageKey": "%s/%s.pdf" % (ASSET_ID, ASSET_ID),
        "AssetURL": "",
        "AssetLanguage": "en",
        "awsRegion": REGION,
        "srcEvent": "aws:s3",
        "srcEventTime": "2023-01-01T00:00:00.000Z",
        "SrcBucket": INGEST_BUCKET,
        "SrcKey": "report.pdf",
        "SrcExtension": "pdf",
        "SrcIP": "192.0.2.10",
        "SrcUser": "",
        "AssetCreationTime": "1672531200",
    }
    base.update(detail)
    return {
        "version": "0",
        "id": "11111111-2222-3333-4444-555555555555",
        "detail-type": detail_type,
        "source": "gdit.me",
        "account": ACCOUNT,
        "time": "2023-01-01T00:00:00Z",
        "region": REGION,
        "resources": [],
        "detail": base,
    }


def asset_row(**overrides):
    row = {
        "AssetId": ASSET_ID,
        "AssetDatabase": "demodb",
        "AssetSize": 1024,
        "AssetType": "pdf",
        "AssetStorage": "store",
        "AssetStorageBucket": "test-s3assets",
        "AssetStorageKey": "%s/%s.pdf" % (ASSET_ID, ASSET_ID),
        "AssetURL": "",
        "AssetLanguage": "",
    }
    row.update(overrides)
    return row


def sqs_wrapped_s3_event(bucket, key, etag=ASSET_ID, size=1024):
    """SQS event whose body is an S3 ObjectCreated notification."""
    body = {"Records": [{
        "eventSource": "aws:s3",
        "awsRegion": REGION,
        "eventTime": "2023-01-01T00:00:00.000Z",
        "requestParameters": {"sourceIPAddress": "192.0.2.10"},
        "s3": {
            "bucket": {"name": bucket, "arn": "arn:aws:s3:::" + bucket},
            "object": {"key": key, "size": size, "eTag": etag},
        },
    }]}
    return {"Records": [{"messageId": "1", "receiptHandle": "rh", "body": json.dumps(body)}]}
