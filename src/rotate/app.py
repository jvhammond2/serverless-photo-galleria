import os
import io
import boto3
from PIL import Image

s3_client = boto3.client('s3')

def handler(event, context):
    try:
        source_bucket = event.get('s3Bucket')
        object_key = event.get('s3Key')
        target_bucket = os.environ.get('THUMBS_BUCKET')

        s3_response = s3_client.get_object(Bucket=source_bucket, Key=object_key)
        image_data = s3_response['Body'].read()

        with Image.open(io.BytesIO(image_data)) as img:
            # Rotate 90 degrees clockwise (expand=True ensures canvas scales to fit new dimensions)
            rotated_img = img.rotate(270, expand=True)

            buffer = io.BytesIO()
            rotated_img.save(buffer, format="JPEG")
            buffer.seek(0)

        output_key = f"rotated-{object_key}"
        s3_client.put_object(Bucket=target_bucket, Key=output_key, Body=buffer, ContentType='image/jpeg')

        return {'statusCode': 200, 'body': f"Rotated {object_key} saved to {output_key}"}
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e