import os
import io
import boto3
from PIL import Image, ImageFilter

# Initialize the S3 client outside the handler to reuse connection
s3_client = boto3.client('s3')

def handler(event, context):
    try:
        # 1. Grab incoming data passed from the Step Function
        source_bucket = event.get('s3Bucket')
        object_key = event.get('s3Key')
        
        # Get the target bucket from our environment variable
        target_bucket = os.environ.get('THUMBS_BUCKET')
        
        print(f"Processing blur for object {object_key} from bucket {source_bucket}")

        # 2. Download the image from S3 into memory
        s3_response = s3_client.get_object(Bucket=source_bucket, Key=object_key)
        image_data = s3_response['Body'].read()

        # 3. Open the image with Pillow and apply the blur
        with Image.open(io.BytesIO(image_data)) as img:
            # Convert to RGB if it's a PNG/RGBA to ensure JPEG compatibility
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
                
            blurred_img = img.filter(ImageFilter.GaussianBlur(radius=10))

            # Save the blurred image back to an in-memory byte buffer
            buffer = io.BytesIO()
            blurred_img.save(buffer, format="JPEG")
            buffer.seek(0)

        # 4. Upload the processed thumbnail back to the Thumbs S3 Bucket
        output_key = f"blurred-{object_key}"
        s3_client.put_object(
            Bucket=target_bucket,
            Key=output_key,
            Body=buffer,
            ContentType='image/jpeg'
        )

        return {
            'statusCode': 200,
            'body': f"Successfully blurred {object_key} and saved to {target_bucket}/{output_key}"
        }

    except Exception as e:
        print(f"Error processing image: {str(e)}")
        raise e