import json
import boto3
import os

s3_client = boto3.client('s3')

def handler(event, context):
    try:
        body = event.get('body', '{}')
        if isinstance(body, str):
            body = json.loads(body)

        file_name = body.get('fileName')
        file_type = body.get('fileType')
        bucket = os.environ.get('ORIGINALS_BUCKET')

        print(f"fileName: {file_name}, fileType: {file_type}, bucket: {bucket}")

        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': bucket,
                'Key': file_name,
                'ContentType': file_type
            },
            ExpiresIn=300
        )

        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': 'https://d1akgo82m60buv.cloudfront.net',
                'Content-Type': 'application/json'
            },
            'body': json.dumps({'uploadUrl': presigned_url})
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': 'https://d1akgo82m60buv.cloudfront.net',
                'Content-Type': 'application/json'
            },
            'body': json.dumps({'error': str(e)})
        }