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
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
                
            buffer = io.BytesIO()
            # Optimize and reduce quality to compress file size
            img.save(buffer, format="JPEG", optimize=True, quality=40)
            buffer.seek(0)

        output_key = f"compressed-{object_key}"
        s3_client.put_object(Bucket=target_bucket, Key=output_key, Body=buffer, ContentType='image/jpeg')

        return {'statusCode': 200, 'body': f"Compressed {object_key} saved to {output_key}"}
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e