import json

from tests.conftest import ASSET_ID, ingestion_event, published_events

JOB_ID = "video-job-1"
VIDEO_KEY = "%s/%s.mp4" % (ASSET_ID, ASSET_ID)


def _sns_event(status="SUCCEEDED"):
    message = {"JobId": JOB_ID, "Status": status, "API": "StartFaceDetection", "JobTag": ASSET_ID,
               "Timestamp": 1672531200000, "Video": {"S3ObjectName": VIDEO_KEY, "S3Bucket": "test-s3assets"}}
    return {"Records": [{"Sns": {"Message": json.dumps(message)}}]}


def test_start_submits_face_detection_job(aws, load_handler, stub):
    handler = load_handler("video_rekognition_facedetection")
    stub(handler.clientRk).add_response(
        "start_face_detection",
        {"JobId": JOB_ID},
        {
            "Video": {"S3Object": {"Bucket": "test-s3assets", "Name": VIDEO_KEY}},
            "NotificationChannel": {
                "SNSTopicArn": aws.env["vid_rkfacedetectSNSarn"], "RoleArn": aws.env["vid_rkfacedetectRolearn"]},
            "FaceAttributes": "ALL",
            "JobTag": ASSET_ID,
        },
    )

    result = handler.LambdaHandler(ingestion_event(AssetType="mp4", AssetStorageKey=VIDEO_KEY), None)

    assert result["statusCode"] == 200


def test_complete_persists_result_and_publishes_metadata_and_faces(aws, load_handler, stub):
    handler = load_handler("video_rekognition_facedetectioncomplete")
    faces = [{"Timestamp": 0, "Face": {"Confidence": 99.5}}, {"Timestamp": 500, "Face": {"Confidence": 98.5}}]
    stubber = stub(handler.clientRk)
    stubber.add_response(
        "get_face_detection",
        {"JobStatus": "SUCCEEDED", "VideoMetadata": {"Codec": "h264", "DurationMillis": 1000},
         "Faces": faces, "NextToken": "page-2"},
        {"JobId": JOB_ID, "MaxResults": 1000},
    )
    stubber.add_response(
        "get_face_detection",
        {"JobStatus": "SUCCEEDED", "Faces": [{"Timestamp": 900, "Face": {"Confidence": 97.5}}]},
        {"JobId": JOB_ID, "MaxResults": 1000, "NextToken": "page-2"},
    )

    result = handler.LambdaHandler(_sns_event(), None)

    assert result["statusCode"] == 200
    stored = json.loads(aws.read("S3RkFacDet", "%s/%s.json" % (ASSET_ID, ASSET_ID)))
    assert stored["VideoMetadata"] == {"Codec": "h264", "DurationMillis": 1000}
    published = published_events(handler)
    assert [p["Detail"]["ProcessType"] for p in published] == [
        "static_vid_vidmetadata", "static_vid_vidfacedetect", "static_vid_vidfacedetect"]
    assert {p["DetailType"] for p in published} == {"vidfacedetect_awsrk"}
    assert published[0]["Detail"]["AssetStorageKey"] == ASSET_ID + ".mp4"
    # Characterization: later pages are fetched but only first-page faces are published.
    assert [p["Detail"]["Enrichments"] for p in published[1:]] == faces


def test_complete_ignores_failed_jobs(aws, load_handler):
    handler = load_handler("video_rekognition_facedetectioncomplete")

    result = handler.LambdaHandler(_sns_event(status="FAILED"), None)

    assert result["statusCode"] == 200
    assert aws.keys("S3RkFacDet") == []
    assert published_events(handler) == []
