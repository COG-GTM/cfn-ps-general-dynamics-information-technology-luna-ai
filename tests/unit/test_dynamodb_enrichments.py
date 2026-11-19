from decimal import Decimal

from tests.conftest import ASSET_ID, ingestion_event


def test_enrichment_event_is_stored_with_decimal_scores(aws, load_handler):
    handler = load_handler("dynamodbEnrichments")
    event = ingestion_event(
        "enrichments",
        ComponentSource="rekognition", ProcessData="", ProcessExtension="jpg", ProcessType="detectlabels",
        ProcessStartTime="1672531200", ProcessId="", ProcessOutputKey=ASSET_ID + "/out.json",
        Enrichments=[{"Name": "Dog", "Confidence": 99.5}],
    )

    result = handler.LambdaHandler(event, None)

    assert result["statusCode"] == 200
    (row,) = aws.items("AssetEnrichmentsTable")
    assert row["AssetId"] == ASSET_ID
    assert row["detail-type"] == "enrichments"
    assert row["ProcessType"] == "detectlabels"
    assert row["Enrichments"] == [{"Name": "Dog", "Confidence": Decimal("99.5")}]
    assert row["id"] == event["id"]
    assert isinstance(row["Timestamp"], Decimal)
