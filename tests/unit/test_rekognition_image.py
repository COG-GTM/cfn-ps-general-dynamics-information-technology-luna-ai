import json

import pytest

from tests.conftest import ASSET_ID, ingestion_event, published_events

IMAGE = {"S3Object": {"Bucket": "test-s3assets", "Name": "%s/%s.jpg" % (ASSET_ID, ASSET_ID)}}
OUTPUT_KEY = "%s/%s.json" % (ASSET_ID, ASSET_ID)


def _event():
    return ingestion_event(AssetType="jpg", AssetStorageKey=IMAGE["S3Object"]["Name"])


def _assert_enrichment_event(handler, process_type, bucket):
    (published,) = published_events(handler)
    assert published["DetailType"] == "enrichments"
    assert published["Detail"]["ComponentSource"] == "rekognition"
    assert published["Detail"]["ProcessType"] == process_type
    assert published["Detail"]["ProcessOutputBucket"] == bucket
    assert published["Detail"]["ProcessOutputKey"] == OUTPUT_KEY
    return published["Detail"]["Enrichments"]


def test_labels_are_indexed_persisted_and_published(aws, load_handler, stub):
    handler = load_handler("rkImgDetectLabels")
    labels = [{"Name": "Dog", "Confidence": 99.5}, {"Name": "Pet", "Confidence": 90.25}]
    stub(handler.clientRk).add_response(
        "detect_labels", {"Labels": labels}, {"Image": IMAGE, "MinConfidence": 80})

    result = handler.LambdaHandler(_event(), None)

    assert result["statusCode"] == 200
    assert json.loads(aws.read("S3RkLblDet", OUTPUT_KEY)) == labels
    assert sorted(i["Term"] for i in aws.items("SearchTable")) == ["Dog", "Pet"]
    (aggregate,) = aws.items("SearchAggregateTable")
    assert aggregate["Context"] == "Dog, Pet,  "
    assert aggregate["EnrichmentKey"] == OUTPUT_KEY
    enrichments = _assert_enrichment_event(handler, "detectlabels", aws.env["S3RkLblDet"])
    assert [e["Name"] for e in enrichments] == ["Dog", "Pet"]


def test_no_labels_raises_and_writes_nothing(aws, load_handler, stub):
    """Characterization of current behaviour: an image with no labels raises UnboundLocalError."""
    handler = load_handler("rkImgDetectLabels")
    stub(handler.clientRk).add_response("detect_labels", {"Labels": []})

    with pytest.raises(UnboundLocalError):
        handler.LambdaHandler(_event(), None)

    assert aws.keys("S3RkLblDet") == []
    assert aws.items("SearchTable") == []
    assert published_events(handler) == []


def test_text_lines_are_indexed_and_words_ignored(aws, load_handler, stub):
    handler = load_handler("rkImgDetectText")
    detections = [
        {"DetectedText": "STOP HERE", "Type": "LINE", "Confidence": 99.5},
        {"DetectedText": "STOP", "Type": "WORD", "Confidence": 99.5},
    ]
    stub(handler.clientRk).add_response(
        "detect_text", {"TextDetections": detections},
        {"Image": IMAGE, "Filters": {"WordFilter": {"MinConfidence": 80.0}}})

    handler.LambdaHandler(_event(), None)

    assert [i["Term"] for i in aws.items("SearchTable")] == ["STOP HERE"]
    assert json.loads(aws.read("S3RkTxtDet", OUTPUT_KEY)) == detections
    enrichments = _assert_enrichment_event(handler, "detecttext", aws.env["S3RkTxtDet"])
    assert enrichments == [{"Name": "STOP HERE", "ParentName": "", "Confidence": 99.5}]


def test_no_text_raises_and_writes_nothing(aws, load_handler, stub):
    """Characterization of current behaviour: an image with no text raises UnboundLocalError."""
    handler = load_handler("rkImgDetectText")
    stub(handler.clientRk).add_response("detect_text", {"TextDetections": []})

    with pytest.raises(UnboundLocalError):
        handler.LambdaHandler(_event(), None)

    assert aws.keys("S3RkTxtDet") == []
    assert published_events(handler) == []


def test_moderation_labels_are_indexed_and_published(aws, load_handler, stub):
    handler = load_handler("rkImgModeration")
    labels = [{"Name": "Violence", "ParentName": "", "Confidence": 91.5}]
    stub(handler.clientRk).add_response(
        "detect_moderation_labels", {"ModerationLabels": labels}, {"Image": IMAGE, "MinConfidence": 80})

    handler.LambdaHandler(_event(), None)

    assert [i["Term"] for i in aws.items("SearchTable")] == ["Violence"]
    assert json.loads(aws.read("S3RkMod", OUTPUT_KEY)) == labels
    assert _assert_enrichment_event(handler, "moderation", aws.env["S3RkMod"]) == labels


def test_clean_image_produces_no_moderation_output(aws, load_handler, stub):
    handler = load_handler("rkImgModeration")
    stub(handler.clientRk).add_response("detect_moderation_labels", {"ModerationLabels": []})

    result = handler.LambdaHandler(_event(), None)

    assert result["statusCode"] == 200
    assert aws.keys("S3RkMod") == []
    assert published_events(handler) == []


def test_faces_are_summarized_without_landmarks(aws, load_handler, stub):
    handler = load_handler("rkImgDetectFaces")
    face = {
        "AgeRange": {"Low": 25, "High": 35},
        "Gender": {"Value": "Female", "Confidence": 99.5},
        "Smile": {"Value": True, "Confidence": 95.0},
        "Landmarks": [{"Type": "eyeLeft", "X": 0.5, "Y": 0.5}],
        "Pose": {"Roll": 1.0, "Yaw": 2.0, "Pitch": 3.0},
        "Quality": {"Brightness": 50.0, "Sharpness": 60.0},
    }
    stub(handler.clientRk).add_response(
        "detect_faces", {"FaceDetails": [face]}, {"Image": IMAGE, "Attributes": ["ALL"]})

    handler.LambdaHandler(_event(), None)

    assert [i["Term"] for i in aws.items("SearchTable")] == ["Female:25-35:Person1"]
    assert json.loads(aws.read("S3RkFacDet", OUTPUT_KEY)) == [face]
    (enrichment,) = _assert_enrichment_event(handler, "detectfaces", aws.env["S3RkFacDet"])
    assert sorted(enrichment) == ["AgeRange", "Gender", "Smile"]


def test_celebrities_are_indexed_with_selected_fields(aws, load_handler, stub):
    handler = load_handler("rkImgDetectCelebrity")
    celebrity = {"Name": "Grace Hopper", "Id": "abc123", "MatchConfidence": 98.5,
                 "Urls": ["www.example.com/gh"], "Face": {"Confidence": 99.5}}
    stub(handler.clientRk).add_response(
        "recognize_celebrities", {"CelebrityFaces": [celebrity], "UnrecognizedFaces": []}, {"Image": IMAGE})

    handler.LambdaHandler(_event(), None)

    assert [i["Term"] for i in aws.items("SearchTable")] == ["Grace Hopper"]
    assert json.loads(aws.read("S3RkCeleb", OUTPUT_KEY)) == [celebrity]
    (enrichment,) = _assert_enrichment_event(handler, "recognizeceleb", aws.env["S3RkCeleb"])
    assert enrichment == {"Name": "Grace Hopper", "Id": "abc123", "MatchConfidence": 98.5,
                          "Urls": ["www.example.com/gh"]}


@pytest.mark.parametrize("module", [
    "rkImgDetectLabels", "rkImgDetectText", "rkImgModeration", "rkImgDetectFaces", "rkImgDetectCelebrity"])
def test_non_ingestion_events_call_rekognition_with_empty_location(aws, load_handler, module):
    """Characterization: other detail-types are not filtered out and reach the service call."""
    from botocore.exceptions import ParamValidationError

    handler = load_handler(module)

    with pytest.raises(ParamValidationError):
        handler.LambdaHandler(ingestion_event("alternateformat"), None)


def test_confidence_without_exact_binary_form_is_rejected_by_dynamodb_serializer(aws, load_handler, stub):
    """Characterization of current behaviour: Decimal(float) is passed to DynamoDB unrounded."""
    import decimal

    handler = load_handler("rkImgDetectLabels")
    stub(handler.clientRk).add_response("detect_labels", {"Labels": [{"Name": "Dog", "Confidence": 87.3}]})

    with pytest.raises((decimal.Inexact, decimal.Rounded)):
        handler.LambdaHandler(_event(), None)
