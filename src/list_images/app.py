import json
import os
import boto3

s3_client = boto3.client('s3')
THUMBS_BUCKET = os.environ.get('THUMBS_BUCKET')

def handler(event, context):
    try:
        response = s3_client.list_objects_v2(Bucket=THUMBS_BUCKET)

        images = []
        for obj in response.get('Contents', []):
            key = obj['Key']
            preview_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': THUMBS_BUCKET, 'Key': key},
                ExpiresIn=300
            )
            images.append({
                'key': key,
                'previewUrl': preview_url
            })

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            },
            'body': json.dumps({'images': images})
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            },
            'body': json.dumps({'error': str(e)})
        }