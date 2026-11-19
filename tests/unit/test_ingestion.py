import json
from decimal import Decimal

from tests.conftest import ASSET_ID, INGEST_BUCKET, published_events, sqs_wrapped_s3_event


def test_ingestion_moves_asset_records_it_and_publishes_event(aws, load_handler):
    aws.put(INGEST_BUCKET, "my report.pdf", b"%PDF-1.4")
    handler = load_handler("ingestion")

    result = handler.LambdaHandler(sqs_wrapped_s3_event(INGEST_BUCKET, "my+report.pdf"), None)

    assert result["statusCode"] == 200
    asset_key = "%s/%s.pdf" % (ASSET_ID, ASSET_ID)
    assert aws.read("S3Assets", asset_key) == b"%PDF-1.4"
    assert aws.keys(INGEST_BUCKET) == []

    (row,) = aws.items("AssetTable")
    assert row["AssetId"] == ASSET_ID
    assert row["AssetType"] == "pdf"
    assert row["AssetStorageBucket"] == aws.env["S3Assets"]
    assert row["AssetStorageKey"] == asset_key
    assert row["SrcKey"] == "my report.pdf"

    (event,) = published_events(handler)
    assert event["EventBusName"] == "me-bus"
    assert event["DetailType"] == "ingestion"
    assert event["Source"] == "gdit.me"
    assert event["Resources"] == ["arn:aws:s3:::" + INGEST_BUCKET]
    assert event["Detail"]["AssetId"] == ASSET_ID
    assert event["Detail"]["AssetStorageKey"] == asset_key
    assert event["Detail"]["SrcIP"] == "192.0.2.10"


def test_reingestion_purges_previous_outputs_for_that_asset_only(aws, load_handler):
    other = "ffffffffffffffffffffffffffffffff"
    aws.put(INGEST_BUCKET, "a.png", b"new")
    for bucket in ("S3txtract", "S3RkLblDet", "S3NlpNer"):
        aws.put(bucket, ASSET_ID + "/old.json")
        aws.put(bucket, other + "/keep.json")
    for asset in (ASSET_ID, other):
        aws.table("AssetFormatsTable").put_item(Item={"AssetId": asset, "FormatType": "txt"})
        aws.table("AssetEnrichmentsTable").put_item(Item={"AssetId": asset, "Timestamp": Decimal("1.5")})
        aws.table("SearchTable").put_item(
            Item={"Term": "Cat", "Context": "aws-detectlabels-" + asset, "AssetId": asset, "Confidence": Decimal(99)})
        aws.table("SearchAggregateTable").put_item(
            Item={"AssetId": asset, "ProcessType": "aws-detectlabels", "Context": "Cat, "})
    handler = load_handler("ingestion")

    handler.LambdaHandler(sqs_wrapped_s3_event(INGEST_BUCKET, "a.png"), None)

    for bucket in ("S3txtract", "S3RkLblDet", "S3NlpNer"):
        assert aws.keys(bucket) == [other + "/keep.json"]
    for table in ("AssetFormatsTable", "AssetEnrichmentsTable", "SearchTable", "SearchAggregateTable"):
        assert [i["AssetId"] for i in aws.items(table)] == [other]
    assert aws.read("S3Assets", "%s/%s.png" % (ASSET_ID, ASSET_ID)) == b"new"


def test_ingestion_processes_every_record_in_the_batch(aws, load_handler):
    aws.put(INGEST_BUCKET, "one.txt")
    aws.put(INGEST_BUCKET, "two.txt")
    first = sqs_wrapped_s3_event(INGEST_BUCKET, "one.txt", etag="a" * 32)
    second = sqs_wrapped_s3_event(INGEST_BUCKET, "two.txt", etag="b" * 32)
    handler = load_handler("ingestion")

    handler.LambdaHandler({"Records": first["Records"] + second["Records"]}, None)

    assert sorted(i["AssetId"] for i in aws.items("AssetTable")) == ["a" * 32, "b" * 32]
    assert len(published_events(handler)) == 2


def test_missing_source_object_is_logged_and_still_recorded(aws, load_handler, capsys):
    """Characterization: copy/delete failures are swallowed, the asset row is still written."""
    handler = load_handler("ingestion")

    handler.LambdaHandler(sqs_wrapped_s3_event(INGEST_BUCKET, "missing.pdf"), None)

    assert "Information:" in capsys.readouterr().out
    assert len(aws.items("AssetTable")) == 1
    assert aws.keys("S3Assets") == []


def test_ehash_returns_unquoted_etag(aws, load_handler):
    aws.put(INGEST_BUCKET, "a.txt", b"abc")
    handler = load_handler("ingestion")

    assert handler.eHash(INGEST_BUCKET, "a.txt") == "900150983cd24fb0d6963f7d28e17f72"


def test_datetime_encoder_serializes_datetimes(aws, load_handler):
    from datetime import datetime

    handler = load_handler("ingestion")

    encoded = json.dumps({"t": datetime(2023, 1, 2, 3, 4, 5)}, cls=handler.DateTimeEncoder)
    assert json.loads(encoded) == {"t": "2023-01-02T03:04:05"}
