import json

from tests.conftest import ASSET_ID, asset_row, ingestion_event, published_events

JOB_ID = "job-123"


def _sns_event(topic_arn, status="SUCCEEDED"):
    message = {"JobId": JOB_ID, "Status": status, "API": "StartDocumentTextDetection",
               "JobTag": ASSET_ID, "Timestamp": 1672531200000}
    return {"Records": [{"Sns": {"TopicArn": topic_arn, "Message": json.dumps(message)}}]}


def test_start_submits_job_records_processing_and_publishes(aws, load_handler, stub):
    handler = load_handler("textractStart")
    event = ingestion_event()
    stub(handler.textract_client).add_response(
        "start_document_text_detection",
        {"JobId": JOB_ID},
        {
            "DocumentLocation": {"S3Object": {
                "Bucket": event["detail"]["AssetStorageBucket"], "Name": event["detail"]["AssetStorageKey"]}},
            "JobTag": ASSET_ID,
            "NotificationChannel": {
                "RoleArn": aws.env["TextractSNSarn"], "SNSTopicArn": aws.env["TextractSNSTopicArn"]},
            "OutputConfig": {"S3Bucket": aws.env["S3txtract"], "S3Prefix": ASSET_ID},
        },
    )

    handler.LambdaHandler(event, None)

    (row,) = aws.items("AssetProcessingTable")
    assert row["AssetId"] == ASSET_ID
    assert row["ProcessId"] == JOB_ID
    assert row["ProcessType"] == "Extract"
    assert row["ProcessOutputKey"] == "output/job-123/"
    (published,) = published_events(handler)
    assert published["DetailType"] == "textractstartedasync"
    assert published["Detail"]["ProcessId"] == JOB_ID
    assert published["Resources"] == [aws.env["S3txtractarn"], aws.env["S3txtractAarn"]]


def test_complete_flattens_output_builds_plain_text_and_publishes(aws, load_handler):
    aws.table("AssetTable").put_item(Item=asset_row())
    page1 = {"Blocks": [{"BlockType": "PAGE"}, {"BlockType": "LINE", "Text": "Hello"},
                        {"BlockType": "WORD", "Text": "Hello"}]}
    page2 = {"Blocks": [{"BlockType": "LINE", "Text": "World"}]}
    aws.put("S3txtract", "%s/%s/1" % (ASSET_ID, JOB_ID), json.dumps(page1))
    aws.put("S3txtract", "%s/%s/2" % (ASSET_ID, JOB_ID), json.dumps(page2))
    aws.put("S3txtract", "%s/%s/.s3_access_check" % (ASSET_ID, JOB_ID))
    handler = load_handler("textractComplete")

    result = handler.LambdaHandler(_sns_event(aws.env["TextractSNSTopicArn"]), None)

    assert result["statusCode"] == 200
    assert aws.keys("S3txtract") == [ASSET_ID + "/1", ASSET_ID + "/2"]
    text_key = "%s/%s.txt" % (ASSET_ID, ASSET_ID)
    assert aws.read("S3txtPlain", text_key) == b"Hello\nWorld\n"
    (fmt,) = aws.items("AssetFormatsTable")
    assert fmt["FormatType"] == "txt"
    assert fmt["AssetStorageBucket"] == aws.env["S3txtPlain"]
    assert fmt["AssetStorageKey"] == text_key
    (published,) = published_events(handler)
    assert published["DetailType"] == "alternateformat"
    assert published["Detail"]["ProcessOutputKey"] == text_key
    assert published["Detail"]["ProcessExtension"] == "txt"


def test_complete_ignores_failed_jobs(aws, load_handler):
    aws.put("S3txtract", "%s/%s/1" % (ASSET_ID, JOB_ID), "{}")
    handler = load_handler("textractComplete")

    handler.LambdaHandler(_sns_event(aws.env["TextractSNSTopicArn"], status="FAILED"), None)

    assert aws.keys("S3txtract") == ["%s/%s/1" % (ASSET_ID, JOB_ID)]
    assert aws.keys("S3txtPlain") == []
    assert published_events(handler) == []


def test_complete_analysis_topic_only_flattens_output(aws, load_handler):
    aws.put("S3txtractA", "%s/%s/1" % (ASSET_ID, JOB_ID), "{}")
    handler = load_handler("textractComplete")

    handler.LambdaHandler(_sns_event(aws.env["TextractASNSTopicArn"]), None)
    handler.LambdaHandler(_sns_event(aws.env["TextractASNSTopicArn"], status="FAILED"), None)

    assert aws.keys("S3txtractA") == [ASSET_ID + "/1"]
    assert published_events(handler) == []


def test_complete_ignores_unknown_topic(aws, load_handler):
    handler = load_handler("textractComplete")

    result = handler.LambdaHandler(_sns_event("arn:aws:sns:us-east-1:123456789012:other"), None)

    assert result["statusCode"] == 200
    assert published_events(handler) == []
