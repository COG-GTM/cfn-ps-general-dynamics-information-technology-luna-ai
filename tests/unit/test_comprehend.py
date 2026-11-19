import io
import json
import tarfile

import pytest

from tests.conftest import ASSET_ID, asset_row, ingestion_event, published_events, sqs_wrapped_s3_event

JOB_ID = "abcdef0123456789"


def _tar_gz(payload):
    data = json.dumps(payload).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("output")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _output_key(kind):
    return "%s/123456789012-%s-%s/output/output.tar.gz" % (ASSET_ID, kind, JOB_ID)


# --- dominant language -------------------------------------------------------

@pytest.mark.parametrize("detail_type,detail,expected_uri", [
    ("ingestion", {"AssetType": "txt"}, "s3://test-s3assets/%s/%s.pdf" % (ASSET_ID, ASSET_ID)),
    ("alternateformat",
     {"ProcessOutputBucket": "test-s3txtplain", "ProcessOutputKey": ASSET_ID + "/a.txt", "ProcessExtension": "txt"},
     "s3://test-s3txtplain/%s/a.txt" % ASSET_ID),
])
def test_language_start_submits_job_for_source_document(aws, load_handler, stub, detail_type, detail, expected_uri):
    handler = load_handler("dominantlanguageStart")
    stub(handler.comprehendClient).add_response(
        "start_dominant_language_detection_job",
        {"JobId": JOB_ID, "JobStatus": "SUBMITTED"},
        {
            "InputDataConfig": {"S3Uri": expected_uri, "InputFormat": "ONE_DOC_PER_FILE"},
            "OutputDataConfig": {"S3Uri": "s3://%s/%s/" % (aws.env["S3NlpLang"], ASSET_ID)},
            "DataAccessRoleArn": aws.env["ComprehendAccessarn"],
            "JobName": ASSET_ID,
        },
    )

    result = handler.LambdaHandler(ingestion_event(detail_type, **detail), None)

    assert result["statusCode"] == 200


def test_language_complete_unpacks_result_updates_asset_and_publishes(aws, load_handler):
    aws.table("AssetTable").put_item(Item=asset_row())
    key = _output_key("LANGUAGE")
    aws.put("S3NlpLang", key, _tar_gz({"Languages": [{"LanguageCode": "en"}, {"LanguageCode": "es"}]}))
    handler = load_handler("dominantlanguageComplete")

    result = handler.LambdaHandler(sqs_wrapped_s3_event(aws.env["S3NlpLang"], key), None)

    assert result["statusCode"] == 200
    assert aws.keys("S3NlpLang") == [ASSET_ID + "/output"]
    (row,) = aws.items("AssetTable")
    assert row["AssetLanguage"] == "en,es"
    (published,) = published_events(handler)
    assert published["DetailType"] == "enrichments"
    assert published["Detail"]["AssetLanguage"] == "en,es"
    assert published["Detail"]["ProcessType"] == "dominantlanguage"


def test_language_complete_skips_non_archive_objects(aws, load_handler):
    key = "%s/123456789012-LANGUAGE-%s/output/manifest.json" % (ASSET_ID, JOB_ID)
    aws.put("S3NlpLang", key)
    handler = load_handler("dominantlanguageComplete")

    handler.LambdaHandler(sqs_wrapped_s3_event(aws.env["S3NlpLang"], key), None)

    assert aws.keys("S3NlpLang") == [key]
    assert published_events(handler) == []


# --- entity detection --------------------------------------------------------

def _expect_entities_job(aws, handler, stub, source_uri):
    stub(handler.clientComprehend).add_response(
        "start_entities_detection_job",
        {"JobId": JOB_ID, "JobStatus": "SUBMITTED"},
        {
            "InputDataConfig": {"S3Uri": source_uri, "InputFormat": "ONE_DOC_PER_FILE"},
            "OutputDataConfig": {"S3Uri": "s3://%s/%s/" % (aws.env["S3NlpNer"], ASSET_ID)},
            "DataAccessRoleArn": aws.env["ComprehendAccessarn"],
            "JobName": ASSET_ID,
            "LanguageCode": "en",
        },
    )


def test_entities_start_uses_plain_text_alternate_format(aws, load_handler, stub):
    aws.table("AssetTable").put_item(Item=asset_row())
    aws.table("AssetFormatsTable").put_item(Item={
        "AssetId": ASSET_ID, "FormatType": "txt", "AssetType": "txt",
        "AssetStorageBucket": "test-s3txtplain", "AssetStorageKey": ASSET_ID + "/a.txt"})
    handler = load_handler("comprehendStart")
    _expect_entities_job(aws, handler, stub, "s3://test-s3txtplain/%s/a.txt" % ASSET_ID)

    result = handler.LambdaHandler(ingestion_event(), None)

    assert result["statusCode"] == 200


def test_entities_start_prefers_original_when_it_is_text(aws, load_handler, stub):
    aws.table("AssetTable").put_item(Item=asset_row(AssetType="txt", AssetStorageKey=ASSET_ID + "/orig.txt"))
    aws.table("AssetFormatsTable").put_item(Item={
        "AssetId": ASSET_ID, "FormatType": "txt", "AssetType": "txt",
        "AssetStorageBucket": "test-s3txtplain", "AssetStorageKey": ASSET_ID + "/a.txt"})
    handler = load_handler("comprehendStart")
    _expect_entities_job(aws, handler, stub, "s3://test-s3assets/%s/orig.txt" % ASSET_ID)

    handler.LambdaHandler(ingestion_event(), None)


def test_entities_start_fails_when_no_alternate_format_row_exists(aws, load_handler):
    """Characterization of current behaviour: a missing AssetFormats row raises KeyError."""
    aws.table("AssetTable").put_item(Item=asset_row(AssetType="txt"))
    handler = load_handler("comprehendStart")

    with pytest.raises(KeyError):
        handler.LambdaHandler(ingestion_event(), None)


def test_entities_complete_indexes_confident_entities_and_publishes(aws, load_handler):
    aws.table("AssetTable").put_item(Item=asset_row())
    key = _output_key("NER")
    entities = {"Entities": [
        {"Type": "PERSON", "Text": "ada lovelace", "Score": 0.99},
        {"Type": "ORGANIZATION", "Text": "analytical engines", "Score": 0.9},
        {"Type": "QUANTITY", "Text": "two", "Score": 0.99},
        {"Type": "LOCATION", "Text": "london", "Score": 0.5},
    ]}
    aws.put("S3NlpNer", key, _tar_gz(entities))
    aws.put("S3NlpNer", ASSET_ID + "/.write_access_check_file.temp")
    handler = load_handler("comprehendComplete")

    result = handler.LambdaHandler(sqs_wrapped_s3_event(aws.env["S3NlpNer"], key), None)

    assert result["statusCode"] == 200
    assert aws.keys("S3NlpNer") == [ASSET_ID + "/output"]
    terms = {i["Term"]: i for i in aws.items("SearchTable")}
    assert sorted(terms) == ["Organization: Analytical Engines", "Person: Ada Lovelace"]
    assert terms["Person: Ada Lovelace"]["Context"] == "aws-comprehend-" + ASSET_ID
    assert float(terms["Person: Ada Lovelace"]["Confidence"]) == 99.0
    (aggregate,) = aws.items("SearchAggregateTable")
    assert aggregate["Context"] == "Person: Ada Lovelace;  "
    assert aggregate["ProcessType"] == "aws-comprehend"
    (published,) = published_events(handler)
    assert published["DetailType"] == "enrichments"
    assert published["Detail"]["ProcessId"] == JOB_ID
    assert published["Detail"]["ProcessType"] == "ner"
    assert [e["Text"] for e in published["Detail"]["Enrichments"]] == [
        "Ada Lovelace", "Analytical Engines"]
